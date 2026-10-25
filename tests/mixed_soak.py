#!/usr/bin/env python3
"""Mixed-load soak at the real workload: 8 concurrent clients for N minutes (stdlib; run from a worker).

  5 x chat:   multi-turn chats (fresh topic every 6 turns), 300-4k token turns, thinking on/off, T 0 / 0.7
  1 x vision: image + text questions with a known answer (graded)
  1 x medium: fresh 50k-200k real-text prompts (cold prefill next to decodes)
  1 x long:   revisits of one ~600k session (prefix-cache hit expected after the first visit)

    python3 mixed_soak.py MINUTES
Reports failures, latency p50/p95 per kind, cache hits for the long worker; exit code = failures.
"""
import base64, random, statistics, struct, sys, threading, time, zlib
import bench

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 30
END = time.time() + MIN * 60
lat = {k: [] for k in ("chat", "vision", "medium", "long")}
fails, events, lock = [], [], threading.Lock()


def png(w, h, pixel):
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))
    ch = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw, 9)) + ch(b"IEND", b"")
COLORS = {"red": (255, 0, 0), "blue": (0, 0, 255), "green": (0, 200, 0), "yellow": (255, 220, 0)}


def call(kind, msgs, max_tokens, **kw):
    try:
        r, dt = bench.chat(msgs, max_tokens, **kw)
        with lock: lat[kind].append(dt)
        return r
    except Exception as e:
        with lock: fails.append((kind, time.strftime("%H:%M:%S"), repr(e)[:200]))
        return None


def chat_worker(seed):
    rnd = random.Random(seed)
    while time.time() < END:
        msgs = [{"role": "user", "content": bench.text_tokens(rnd.randint(300, 4000), rnd.randrange(10**6)) + "\n\nSummarise the above in two sentences."}]
        for turn in range(6):
            if time.time() >= END: break
            think = rnd.random() < 0.3
            r = call("chat", msgs, 1500 if think else 200, temperature=rnd.choice([0, 0.7]), chat_template_kwargs={"thinking": think})
            if r is None: break
            msgs += [{"role": "assistant", "content": r["choices"][0]["message"]["content"] or "ok"},
                     {"role": "user", "content": rnd.choice(["Why?", "Give an example.", "Shorter please.", "Name one function above.", "What would you change?"])}]


def vision_worker():
    rnd = random.Random(7)
    while time.time() < END:
        name = rnd.choice(list(COLORS)); img = base64.b64encode(png(96, 96, lambda x, y: COLORS[name])).decode()
        r = call("vision", [{"role": "user", "content": [{"type": "text", "text": "What colour is this image? One word."},
                 {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img}}]}], 10)
        if r is not None and name not in (r["choices"][0]["message"]["content"] or "").lower():
            with lock: fails.append(("vision-wrong", time.strftime("%H:%M:%S"), f"{name} -> {r['choices'][0]['message']['content']!r}"))


def medium_worker():
    rnd = random.Random(11)
    while time.time() < END:
        call("medium", [{"role": "user", "content": bench.text_tokens(rnd.randint(50_000, 200_000), rnd.randrange(10**6)) + "\n\nReply OK."}], 8)


def long_worker():
    doc = bench.text_tokens_exact(600_000, seed=4242)
    while time.time() < END:
        r = call("long", [{"role": "user", "content": doc + "\n\nName one file shown above."}], 24)
        if r is not None:
            c = (r["usage"].get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            with lock: events.append(c / r["usage"]["prompt_tokens"])


ts = [threading.Thread(target=chat_worker, args=(i,)) for i in range(5)] + \
     [threading.Thread(target=vision_worker), threading.Thread(target=medium_worker), threading.Thread(target=long_worker)]
for t in ts: t.start()
for t in ts: t.join()

print(f"mixed soak {MIN:.0f} min, 8 concurrent clients")
for k, v in lat.items():
    if v:
        v = sorted(v); print(f"  {k:<7} n={len(v):>4}  p50 {statistics.median(v):6.1f}s  p95 {v[int(0.95 * (len(v) - 1))]:6.1f}s")
print(f"  long-session revisits: {len(events)} (first is cold); hits after the first: "
      f"{sum(e > 0.95 for e in events[1:])}/{max(0, len(events) - 1)}")
print(f"  failures: {len(fails)}"); [print("   ", f) for f in fails[:20]]
sys.exit(len(fails))
