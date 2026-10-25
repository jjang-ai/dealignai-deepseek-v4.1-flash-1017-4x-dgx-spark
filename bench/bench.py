#!/usr/bin/env python3
"""DSV4.1-Flash-1017 TP4 ring bench (stdlib only; run from a worker, not the head).

  bench.py decode  [--conc 1,2,4,8] [--kind prose|code] [--tokens 256] [--reps 2]
  bench.py prefill [--lens 4000,16000,32000,64000,128000,262000]
  bench.py needle  [--len 1000000] [--streams 4] [--depth 0.37]
  bench.py cache   [--len 200000] [--sessions 3]      second visits of N sessions hit the prefix cache
  bench.py lru     [--len 1000000] [--sessions 5]     N-1 sessions stay cached; the Nth evicts the oldest

Speeds are usage.completion_tokens / elapsed (never SSE chunk counts: a DSpark step carries
several tokens). Prompt text is real source/doc text from the local filesystem, offset per
request so nothing comes from the prefix cache unless a test wants it to.
"""
import argparse, concurrent.futures as cf, json, os, random, statistics, time, urllib.request

URL = os.environ.get("DSV41_URL", "http://10.10.100.1:8888")
HEADERS = {"Content-Type": "application/json"}
if os.environ.get("DSV41_API_KEY"):
    HEADERS["Authorization"] = "Bearer " + os.environ["DSV41_API_KEY"]
MODEL = os.environ.get("DSV41_MODEL", "DeepSeek-V4.1-Flash-1017-UNCENSORED")
CHARS_PER_TOK = 3.3  # measured below via usage.prompt_tokens and corrected per run

PROSE = ["Write a detailed, original short story about a lighthouse keeper who discovers that the light "
         "has been signalling to something far out at sea. At least 600 words.",
         "Explain, in flowing prose with no lists, how the Roman Republic turned into an empire, "
         "covering the social, economic and military causes.",
         "Describe a long train journey across Siberia in winter from the point of view of a retired violinist."]
CODE = ["Write a complete Python module implementing an LRU cache with TTL expiry, thread safety, "
        "and a decorator API, with docstrings and unit tests.",
        "Write a C program that parses a CSV file with quoted fields and prints per-column statistics.",
        "Implement a Rust async TCP chat server with tokio, broadcast channels and graceful shutdown."]


def post(path, body, timeout=3600):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def chat(messages, max_tokens, temperature=0.0, thinking=False, **kw):
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"thinking": thinking}}
    body.update(kw)
    t0 = time.time()
    r = post("/v1/chat/completions", body)
    return r, time.time() - t0


_CORPUS = None
def corpus():
    """~60 MB of real text (Python stdlib + site-packages sources, man/doc text), deterministic order.
    os.walk without following symlinks (glob '**' loops forever on /usr/share/doc symlinks).
    Cached in ~/.cache/dsv41-bench-corpus.txt."""
    global _CORPUS
    if _CORPUS is not None:
        return _CORPUS
    cache = os.path.expanduser("~/.cache/dsv41-bench-corpus.txt")
    if os.path.exists(cache):
        _CORPUS = open(cache, encoding="utf-8").read(); return _CORPUS
    parts, n = [], 0
    for root in ("/usr/lib/python3.12", "/usr/lib/python3/dist-packages", "/usr/share/doc", "/usr/include"):
        for d, subs, files in os.walk(root, followlinks=False):
            subs.sort()
            for f in sorted(files):
                if not f.endswith((".py", ".txt", ".md", ".rst", ".h", ".c")):
                    continue
                fp = os.path.join(d, f)
                if os.path.islink(fp):
                    continue
                try:
                    s = open(fp, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                parts.append(f"\n# ==== {fp} ====\n{s}"); n += len(s)
                if n > 60_000_000:
                    break
            if n > 60_000_000:
                break
    _CORPUS = "".join(parts)
    os.makedirs(os.path.dirname(cache), exist_ok=True); open(cache, "w", encoding="utf-8").write(_CORPUS)
    return _CORPUS


def text_tokens(ntok, seed):
    c = corpus(); n = int(ntok * CHARS_PER_TOK)
    start = (seed * 7_919_993) % max(1, len(c) - n - 1)
    return c[start:start + n]


def cmd_decode(a):
    prompts = PROSE if a.kind == "prose" else CODE
    print(f"decode kind={a.kind} tokens={a.tokens} (aggregate tok/s, per-stream in brackets)")
    for c in [int(x) for x in a.conc.split(",")]:
        aggs, pers = [], []
        for rep in range(a.reps + 1):  # first wave is a warm-up
            def one(i):
                p = prompts[i % len(prompts)] + f" (variant {rep}-{i})"
                r, dt = chat([{"role": "user", "content": p}], a.tokens)
                return r["usage"]["completion_tokens"], dt
            t0 = time.time()
            with cf.ThreadPoolExecutor(c) as ex:
                res = list(ex.map(one, range(c)))
            wall = time.time() - t0
            if rep == 0:
                continue
            aggs.append(sum(n for n, _ in res) / wall)
            pers.append(statistics.mean(n / dt for n, dt in res))
        print(f"  c{c:<3} {statistics.median(aggs):8.1f}  ({statistics.median(pers):.1f})", flush=True)


def cmd_prefill(a):
    global CHARS_PER_TOK
    print("prefill cold, unique real text, max_tokens=1")
    for i, L in enumerate(int(x) for x in a.lens.split(",")):
        t = text_tokens(L, seed=int(time.time()) + i)
        r, dt = chat([{"role": "user", "content": t + "\n\nReply with OK."}], 1)
        pt = r["usage"]["prompt_tokens"]
        CHARS_PER_TOK = CHARS_PER_TOK * L / pt if pt else CHARS_PER_TOK
        cached = (r["usage"].get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        print(f"  {pt:>9} tok  {dt:7.1f}s  {pt/dt:7.0f} tok/s  cached={cached}", flush=True)


def count_tokens(text):
    return post("/tokenize", {"model": MODEL, "prompt": text})["count"]


def text_tokens_exact(ntok, seed):
    """Real text of exactly ~ntok tokens (server tokenizer): scale the char length twice, then trim."""
    c = corpus(); start = (seed * 7_919_993) % max(1, len(c) - int(ntok * 6) - 1)
    n = int(ntok * CHARS_PER_TOK)
    for _ in range(3):
        got = count_tokens(c[start:start + n])
        if abs(got - ntok) <= max(256, ntok // 2000):
            break
        n = int(n * ntok / got)
    while count_tokens(c[start:start + n]) > ntok:
        n = int(n * 0.995)
    return c[start:start + n]


def needle_prompt(L, depth, key, val, seed):
    body = text_tokens_exact(L - 64, seed)
    cut = int(len(body) * depth)
    needle = f"\n\nIMPORTANT FACT: the secret passphrase for vault {key} is {val}.\n\n"
    q = f"\n\nWhat is the secret passphrase for vault {key}? Answer with the passphrase only."
    return body[:cut] + needle + body[cut:] + q


def cmd_needle(a):
    print(f"needle: {a.streams} concurrent streams x ~{a.len} tokens, depth {a.depth}")
    rnd = random.Random(int(time.time()))
    jobs = [(f"V{rnd.randrange(10**6)}", f"{rnd.choice(['amber','cobalt','violet','saffron'])}-{rnd.randrange(10**6)}") for _ in range(a.streams)]
    def one(i):
        k, v = jobs[i]
        depth = a.depth if a.streams == 1 else [0.1, 0.37, 0.63, 0.9, 0.25, 0.5, 0.75, 0.95][i % 8]
        try:
            r, dt = chat([{"role": "user", "content": needle_prompt(a.len, depth, k, v, seed=rnd.randrange(10**9))}], 32)
        except Exception as e:  # keep the other streams' results; report this one
            body = getattr(e, "read", lambda: b"")()[:200]
            return 0, 0.0, False, f"depth={depth} ERROR {e} {body!r}"
        out = r["choices"][0]["message"]["content"] or ""
        return r["usage"]["prompt_tokens"], dt, v in out, f"depth={depth} " + out.strip()[:60]
    t0 = time.time()
    with cf.ThreadPoolExecutor(a.streams) as ex:
        res = list(ex.map(one, range(a.streams)))
    for pt, dt, ok, out in res:
        print(f"  {pt:>9} tok  {dt:7.1f}s  {'PASS' if ok else 'FAIL'}  {out!r}")
    tot = sum(r[0] for r in res) or 1
    print(f"  all {sum(r[2] for r in res)}/{len(res)} pass; {tot} prompt tokens in {time.time()-t0:.1f}s wall "
          f"({tot/(time.time()-t0):.0f} tok/s aggregate prefill)")


def cmd_cache(a):
    """Sessions with long distinct prefixes; follow-up turns should hit the radix cache, and the
    oldest session must be the one evicted when the pool fills (LRU)."""
    print(f"cache: {a.sessions} sessions x ~{a.len} tokens")
    docs = [text_tokens(a.len, seed=1000 + s) for s in range(a.sessions)]
    for rnd in range(2):
        for s, d in enumerate(docs):
            r, dt = chat([{"role": "user", "content": d + f"\n\nQuestion {rnd}: name one function defined above."}], 16)
            u = r["usage"]; cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            print(f"  round{rnd} session{s} prompt={u['prompt_tokens']} cached={cached} {dt:6.1f}s", flush=True)


def cmd_lru(a):
    """Prefix-cache retention + LRU rotation at full scale.
    Phase A: load sessions 0..N-2 cold, then revisit them in order 0..N-2 -> every revisit must hit,
             and session 0 is now the least recently used.
    Phase B: send session N-1 cold; it does not fit, so the radix cache must evict the LRU prefix (session 0).
    Phase C: revisit 1..N-2 (must still hit), then 0 (expected miss: it was rotated out)."""
    docs = [text_tokens_exact(a.len, seed=5000 + i) for i in range(a.sessions)]
    def ask(i, tag):
        r, dt = chat([{"role": "user", "content": docs[i] + "\n\nIn one short sentence, what is the first file shown above?"}], 24)
        u = r["usage"]; cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        print(f"  {tag:<9} session{i} prompt={u['prompt_tokens']:>8} cached={cached:>8} ({100*cached/max(1,u['prompt_tokens']):5.1f}%) {dt:7.1f}s", flush=True)
        return cached / max(1, u["prompt_tokens"])
    n = a.sessions
    print(f"lru: {n} sessions x ~{a.len} tokens; pool must hold {n-1}, not {n}")
    for i in range(n - 1): ask(i, "cold")
    hitA = [ask(i, "revisit") for i in range(n - 1)]
    ask(n - 1, "cold-new")
    hitC = {i: ask(i, "after") for i in list(range(1, n - 1)) + [0]}
    okA = all(h > 0.95 for h in hitA)
    okC = all(hitC[i] > 0.95 for i in range(1, n - 1)) and hitC[0] < 0.5
    print(f"  retention (all {n-1} revisits hit): {'PASS' if okA else 'FAIL'}")
    print(f"  LRU rotation (oldest evicted, recent kept): {'PASS' if okC else 'FAIL'}  session0 hit={hitC[0]:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("decode"); d.add_argument("--conc", default="1,2,4,8"); d.add_argument("--kind", default="prose")
    d.add_argument("--tokens", type=int, default=256); d.add_argument("--reps", type=int, default=2)
    p = sp.add_parser("prefill"); p.add_argument("--lens", default="4000,16000,32000,64000,128000,262000")
    n = sp.add_parser("needle"); n.add_argument("--len", type=int, default=1_040_000); n.add_argument("--streams", type=int, default=4)
    n.add_argument("--depth", type=float, default=0.37)
    c = sp.add_parser("cache"); c.add_argument("--len", type=int, default=200_000); c.add_argument("--sessions", type=int, default=3)
    l = sp.add_parser("lru"); l.add_argument("--len", type=int, default=1_000_000); l.add_argument("--sessions", type=int, default=5)
    a = ap.parse_args(); globals()["cmd_" + a.cmd](a)
