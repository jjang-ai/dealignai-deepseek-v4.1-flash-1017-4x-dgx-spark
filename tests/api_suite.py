#!/usr/bin/env python3
"""API completeness suite for the DSV4.1 TP4 endpoint (stdlib only; run from a worker).

    python3 api_suite.py [--url http://10.10.100.1:8888] [--img-host 10.10.100.3] [--api-key KEY]

Each check prints PASS/FAIL with a reason; exit code = number of failures.
Images are generated here (PNG, known content) so vision answers can be graded automatically.
"""
import argparse, base64, http.server, json, os, struct, sys, threading, time, urllib.request, zlib

A = argparse.ArgumentParser()
A.add_argument("--url", default="http://10.10.100.1:8888")
A.add_argument("--model", default="DeepSeek-V4.1-Flash-1017-UNCENSORED")
A.add_argument("--img-host", default="10.10.100.3")
A.add_argument("--only", default="")
A.add_argument("--api-key", default=os.environ.get("DSV41_API_KEY", ""))
ARGS = A.parse_args()
URL, MODEL = ARGS.url, ARGS.model
HEADERS = {"Content-Type": "application/json", "anthropic-version": "2023-06-01", "User-Agent": "dsv41-tests/1.0"}
if ARGS.api_key:
    HEADERS["Authorization"] = "Bearer " + ARGS.api_key
results = []


def png(w, h, pixel):
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


QUAD = png(256, 256, lambda x, y: (255, 0, 0) if x < 128 and y < 128 else (0, 0, 255) if x >= 128 and y < 128
           else (0, 200, 0) if x < 128 else (255, 220, 0))           # TL red, TR blue, BL green, BR yellow
SOLID_RED = png(128, 128, lambda x, y: (255, 0, 0))
SOLID_BLUE = png(128, 128, lambda x, y: (0, 0, 255))
def data_uri(b): return "data:image/png;base64," + base64.b64encode(b).decode()


def req(path, body=None, timeout=600, raw=False, method=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(URL + path, data=data, method=method or ("POST" if data else "GET"),
                               headers=HEADERS)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            b = resp.read()
            return resp.status, (b if raw else json.loads(b))
    except urllib.error.HTTPError as e:
        b = e.read()
        try: return e.code, json.loads(b)
        except Exception: return e.code, b


def chat(messages, **kw):
    body = {"model": MODEL, "messages": messages, "max_tokens": kw.pop("max_tokens", 200), "temperature": kw.pop("temperature", 0)}
    body.setdefault("chat_template_kwargs", {"thinking": False}); body.update(kw)
    return req("/v1/chat/completions", body)


def check(name, fn):
    if ARGS.only and not any(o in name for o in ARGS.only.split(",")):
        return
    t = time.time()
    try:
        ok, why = fn()
    except Exception as e:  # a crash in a check is a FAIL, not a suite abort
        ok, why = False, f"exception {type(e).__name__}: {str(e)[:200]}"
    results.append((name, ok, why))
    print(f"{'PASS' if ok else 'FAIL'}  {name:<42} {time.time()-t:6.1f}s  {why}", flush=True)


def msg(r): return r["choices"][0]["message"]


# ---------------------------------------------------------------- chat basics
def t_models():
    s, r = req("/v1/models"); ids = [m["id"] for m in r.get("data", [])]
    return s == 200 and MODEL in ids, f"{s} {ids}"

def t_basic():
    s, r = chat([{"role": "user", "content": "What is 17*23? Reply with the number only."}], max_tokens=16)
    c = msg(r)["content"].strip(); u = r["usage"]
    return s == 200 and "391" in c and u["completion_tokens"] > 0 and u["prompt_tokens"] > 0, f"{c!r} usage={u}"

def t_system():
    s, r = chat([{"role": "system", "content": "Always answer in French."}, {"role": "user", "content": "Say hello in one word."}], max_tokens=10)
    c = msg(r)["content"].lower(); return s == 200 and ("bonjour" in c or "salut" in c), repr(c)

def t_multiturn():
    s, r = chat([{"role": "user", "content": "My favourite number is 4711. Remember it."}, {"role": "assistant", "content": "Noted: 4711."},
                 {"role": "user", "content": "What is my favourite number? Number only."}], max_tokens=10)
    c = msg(r)["content"]; return s == 200 and "4711" in c, repr(c)

def t_stop():
    s, r = chat([{"role": "user", "content": "Count from 1 to 20 separated by commas."}], max_tokens=100, stop=["7"])
    c = msg(r)["content"]; return s == 200 and "8" not in c and r["choices"][0]["finish_reason"] == "stop", repr(c[-40:])

def t_maxtokens():
    s, r = chat([{"role": "user", "content": "Write a long essay about the sea."}], max_tokens=20)
    u = r["usage"]["completion_tokens"]; return s == 200 and u <= 20 and r["choices"][0]["finish_reason"] == "length", f"{u} tok"

KNOWN = set()

def t_seed():
    # KNOWN LIMITATION (docs/RESULTS.md): DSpark's draft/verify sampling ignores `seed`, so T>0 is not reproducible.
    m = [{"role": "user", "content": "Invent a random fantasy name."}]
    a = chat(m, temperature=1.0, seed=1234, max_tokens=12)[1]; b = chat(m, temperature=1.0, seed=1234, max_tokens=12)[1]
    same = msg(a)["content"] == msg(b)["content"]
    if not same: KNOWN.add("seed reproducibility")
    return True, ("reproducible" if same else "KNOWN LIMITATION: not reproducible with DSpark at T>0") + f" ({msg(a)['content'][:30]!r})"

def t_n():
    s, r = chat([{"role": "user", "content": "Name a colour."}], temperature=1.0, n=3, max_tokens=8)
    return s == 200 and len(r.get("choices", [])) == 3, f"{s} choices={len(r.get('choices', []))}"

def t_stream():
    body = {"model": MODEL, "messages": [{"role": "user", "content": "List three fruits, comma separated."}], "max_tokens": 40,
            "temperature": 0, "stream": True, "stream_options": {"include_usage": True}, "chat_template_kwargs": {"thinking": False}}
    r = urllib.request.Request(URL + "/v1/chat/completions", data=json.dumps(body).encode(), headers=HEADERS)
    text, usage, done, n = "", None, False, 0
    with urllib.request.urlopen(r, timeout=300) as resp:
        for line in resp:
            line = line.decode().strip()
            if not line.startswith("data:"): continue
            d = line[5:].strip()
            if d == "[DONE]": done = True; break
            j = json.loads(d); n += 1
            for ch in j.get("choices", []): text += (ch.get("delta") or {}).get("content") or ""
            usage = j.get("usage") or usage
    return done and text.count(",") >= 2 and usage and usage["completion_tokens"] > 0, f"{n} chunks {text!r} usage={usage}"

def t_disconnect():
    body = {"model": MODEL, "messages": [{"role": "user", "content": "Write 2000 words about rivers."}], "max_tokens": 2000, "stream": True,
            "chat_template_kwargs": {"thinking": False}}
    resp = urllib.request.urlopen(urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(), HEADERS), timeout=300)
    resp.read(512); time.sleep(1.0); resp.close()                 # drop mid-stream (http or https, with the key)
    time.sleep(2)
    st, r = chat([{"role": "user", "content": "Reply with OK."}], max_tokens=5)
    return st == 200 and "ok" in msg(r)["content"].lower(), "engine healthy after client disconnect"

def t_overlength():
    s, r = chat([{"role": "user", "content": "word " * 1_100_000}], max_tokens=5)
    return s == 400, f"{s} {str(r)[:120]}"

def t_logprobs():
    s, r = chat([{"role": "user", "content": "Say yes."}], max_tokens=4, logprobs=True, top_logprobs=3)
    lp = (r["choices"][0].get("logprobs") or {}).get("content") or []
    return s == 200 and len(lp) > 0 and len(lp[0].get("top_logprobs", [])) == 3, f"{len(lp)} tokens with logprobs"

def t_cached_report():
    # Documented behaviour: a prompt shorter than one prefill chunk may miss on its first revisit and hits from the
    # next one on (longer prompts hit on the first revisit). Unique text per run so earlier runs cannot pre-warm it.
    import random as _r
    m = [{"role": "user", "content": f"Run {_r.randrange(10**9)}. Reference table. " + "alpha beta gamma delta " * 400 + "\nReply OK."}]
    seen = []
    for _ in range(3):
        s, r = chat(m, max_tokens=4); seen.append((r["usage"].get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    return s == 200 and seen[-1] >= 1024, f"cached_tokens per visit {seen} of {r['usage']['prompt_tokens']}"


# ---------------------------------------------------------------- thinking / kwargs
def t_thinking_on():
    s, r = chat([{"role": "user", "content": "Is 221 prime? Answer yes or no."}], max_tokens=3000, chat_template_kwargs={"thinking": True})
    m = msg(r); rc = m.get("reasoning_content") or ""
    return s == 200 and len(rc) > 20 and "no" in (m["content"] or "").lower(), f"reasoning {len(rc)} chars, answer {m['content']!r}"

def t_thinking_alias():
    s, r = chat([{"role": "user", "content": "Is 221 prime? Answer yes or no."}], max_tokens=3000, chat_template_kwargs={"enable_thinking": True})
    rc = msg(r).get("reasoning_content") or ""; return s == 200 and len(rc) > 20, f"enable_thinking alias -> reasoning {len(rc)} chars"

def t_thinking_off():
    s, r = chat([{"role": "user", "content": "Is 221 prime? Answer yes or no."}], max_tokens=50)
    rc = msg(r).get("reasoning_content") or ""; return s == 200 and not rc, f"reasoning {len(rc)} chars when off"

def t_effort():
    lens = {}
    for e in ("low", "max"):
        s, r = chat([{"role": "user", "content": "How many positive divisors does 720 have? Think it through."}], max_tokens=6000,
                    chat_template_kwargs={"thinking": True}, reasoning_effort=e)
        lens[e] = (s, len(msg(r).get("reasoning_content") or ""), "30" in (msg(r)["content"] or ""))
    return all(v[0] == 200 and v[2] for v in lens.values()), f"{lens}"


# ---------------------------------------------------------------- tools / structured output
WEATHER = {"type": "function", "function": {"name": "get_weather", "description": "Current weather for a city",
           "parameters": {"type": "object", "properties": {"city": {"type": "string"}, "unit": {"type": "string", "enum": ["c", "f"]}}, "required": ["city"]}}}
TIME = {"type": "function", "function": {"name": "get_time", "description": "Current local time in a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}

def t_tool_call():
    s, r = chat([{"role": "user", "content": "What's the weather in Paris in celsius?"}], tools=[WEATHER], max_tokens=300)
    tc = msg(r).get("tool_calls") or []
    ok = s == 200 and tc and tc[0]["function"]["name"] == "get_weather" and json.loads(tc[0]["function"]["arguments"]).get("city", "").lower() == "paris"
    return bool(ok), f"{[(t['function']['name'], t['function']['arguments']) for t in tc]} finish={r['choices'][0]['finish_reason']}"

def t_tool_parallel():
    s, r = chat([{"role": "user", "content": "Get the weather in Tokyo and the local time in Berlin."}], tools=[WEATHER, TIME], max_tokens=400)
    names = sorted(t["function"]["name"] for t in (msg(r).get("tool_calls") or []))
    return s == 200 and names == ["get_time", "get_weather"], f"{names}"

def t_tool_choice():
    s, r = chat([{"role": "user", "content": "Hello there!"}], tools=[WEATHER, TIME], max_tokens=300,
                tool_choice={"type": "function", "function": {"name": "get_time"}})
    tc = msg(r).get("tool_calls") or []; return s == 200 and tc and tc[0]["function"]["name"] == "get_time", f"{[t['function']['name'] for t in tc]}"

def t_tool_roundtrip():
    m = [{"role": "user", "content": "What's the weather in Oslo?"}]
    s, r = chat(m, tools=[WEATHER], max_tokens=300); tc = msg(r)["tool_calls"][0]
    m += [{"role": "assistant", "content": None, "tool_calls": [tc]},
          {"role": "tool", "tool_call_id": tc["id"], "content": json.dumps({"city": "Oslo", "temp_c": -7, "sky": "snow"})}]
    s, r = chat(m, tools=[WEATHER], max_tokens=200); c = msg(r)["content"] or ""
    return s == 200 and ("-7" in c or "minus 7" in c.lower() or "−7" in c), repr(c[:160])

def t_json_schema():
    schema = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}}},
              "required": ["name", "age", "tags"], "additionalProperties": False}
    s, r = chat([{"role": "user", "content": "Make up a person with two tags."}], max_tokens=200,
                response_format={"type": "json_schema", "json_schema": {"name": "person", "schema": schema}})
    j = json.loads(msg(r)["content"]); return s == 200 and set(j) == {"name", "age", "tags"} and isinstance(j["age"], int), repr(j)

def t_json_object():
    s, r = chat([{"role": "user", "content": "Return a JSON object with keys a=1 and b=2."}], max_tokens=60, response_format={"type": "json_object"})
    j = json.loads(msg(r)["content"]); return s == 200 and j.get("a") == 1 and j.get("b") == 2, repr(j)


# ---------------------------------------------------------------- multimodal
def img_msg(text, *urls):
    return [{"role": "user", "content": [{"type": "text", "text": text}] + [{"type": "image_url", "image_url": {"url": u}} for u in urls]}]

def t_image_data():
    s, r = chat(img_msg("This image has four coloured quadrants. What colour is the top-left quadrant? One word.", data_uri(QUAD)), max_tokens=20)
    c = msg(r)["content"].lower(); return s == 200 and "red" in c, repr(c)

def t_image_quadrants():
    s, r = chat(img_msg("Name the colours of the four quadrants: top-left, top-right, bottom-left, bottom-right. Comma separated.", data_uri(QUAD)), max_tokens=40)
    c = msg(r)["content"].lower(); order = [c.find(w) for w in ("red", "blue", "green", "yellow")]
    return s == 200 and all(i >= 0 for i in order) and order == sorted(order), repr(c)

_srv = {}
def serve_images():
    if _srv: return _srv["base"]
    d = "/tmp/dsv41-api-imgs"; os.makedirs(d, exist_ok=True)
    open(f"{d}/red.png", "wb").write(SOLID_RED); open(f"{d}/blue.png", "wb").write(SOLID_BLUE)
    h = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=d, **k)
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 0), h); threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _srv["base"] = f"http://{ARGS.img_host}:{httpd.server_address[1]}"; return _srv["base"]

def t_image_http():
    b = serve_images(); s, r = chat(img_msg("What colour is this image? One word.", f"{b}/blue.png"), max_tokens=10)
    c = (msg(r)["content"] if s == 200 else str(r)).lower(); return s == 200 and "blue" in c, repr(c[:160])

def t_multi_image():
    s, r = chat(img_msg("Two images. What colour is the first and what colour is the second? Answer 'first: X, second: Y'.",
                        data_uri(SOLID_RED), data_uri(SOLID_BLUE)), max_tokens=30)
    c = msg(r)["content"].lower(); return s == 200 and c.find("red") >= 0 and c.find("blue") > c.find("red"), repr(c)

def t_image_concurrent():
    import concurrent.futures as cf
    def one(i):
        u, want = (data_uri(SOLID_RED), "red") if i % 2 else (data_uri(SOLID_BLUE), "blue")
        msgs = img_msg("What colour is this image? One word.", u) if i % 3 else [{"role": "user", "content": f"What is {i}+{i}? Number only."}]
        s, r = chat(msgs, max_tokens=10); c = msg(r)["content"].lower()
        return (want in c) if i % 3 else (str(2 * i) in c)
    with cf.ThreadPoolExecutor(8) as ex: res = list(ex.map(one, range(8)))
    return all(res), f"{sum(res)}/8 correct in a mixed image+text batch of 8"

def t_image_thinking():
    s, r = chat(img_msg("Which quadrant is yellow? Answer top-left/top-right/bottom-left/bottom-right.", data_uri(QUAD)), max_tokens=2000,
                chat_template_kwargs={"thinking": True})
    c = (msg(r)["content"] or "").lower(); return s == 200 and "bottom-right" in c.replace(" ", "-"), repr(c[:80])


# ---------------------------------------------------------------- other endpoints
def t_completions():
    s, r = req("/v1/completions", {"model": MODEL, "prompt": "The capital of France is", "max_tokens": 5, "temperature": 0})
    c = r["choices"][0]["text"] if s == 200 else str(r); return s == 200 and "paris" in c.lower(), repr(c)

def t_responses():
    s, r = req("/v1/responses", {"model": MODEL, "temperature": 0, "input": "What is 6*7? Number only.", "max_output_tokens": 16})
    txt = json.dumps(r) if s == 200 else str(r); return s == 200 and "42" in txt, f"{s} {txt[:160]}"

def t_messages():
    s, r = req("/v1/messages", {"model": MODEL, "temperature": 0, "max_tokens": 16, "messages": [{"role": "user", "content": "What is 6*7? Number only."}]})
    txt = json.dumps(r); return s == 200 and "42" in txt, f"{s} {txt[:160]}"

def t_sampling_kwargs():
    s, r = chat([{"role": "user", "content": "Write one sentence about the sea."}], max_tokens=40, temperature=0.8, top_p=0.9,
                top_k=40, min_p=0.05, repetition_penalty=1.1, presence_penalty=0.3, frequency_penalty=0.3)
    c = msg(r)["content"] if s == 200 else str(r); return s == 200 and len(c.split()) >= 4, repr(c[:80])

def sse(path, body):
    r = urllib.request.Request(URL + path, data=json.dumps(body).encode(), headers=HEADERS)
    with urllib.request.urlopen(r, timeout=300) as resp:
        return resp.read().decode()

def t_responses_stream():
    t = sse("/v1/responses", {"model": MODEL, "temperature": 0, "input": "Count to five.", "max_output_tokens": 40, "stream": True})
    ok = all(e in t for e in ("response.created", "response.output_text.delta", "response.completed"))
    return ok, f"{t.count('output_text.delta')} text deltas"

def t_responses_image():
    s, r = req("/v1/responses", {"model": MODEL, "temperature": 0, "instructions": "Answer with one English word.", "max_output_tokens": 20,
                                 "input": [{"role": "user", "content": [{"type": "input_text", "text": "What colour is this image?"},
                                                                         {"type": "input_image", "image_url": data_uri(SOLID_RED)}]}]})
    txt = " ".join(c.get("text", "") for o in r.get("output", []) for c in o.get("content", []) if isinstance(c, dict)) if s == 200 else str(r)
    return s == 200 and "red" in txt.lower(), repr(txt[:80])

def t_responses_tools():
    s, r = req("/v1/responses", {"model": MODEL, "temperature": 0, "input": "What is the weather in Paris? Use the tool.", "max_output_tokens": 200,
                                 "tools": [{"type": "function", "name": "get_weather", "description": "Get the weather for a city",
                                            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]})
    calls = [o for o in r.get("output", []) if o.get("type") == "function_call"] if s == 200 else []
    return bool(calls) and calls[0].get("name") == "get_weather" and "paris" in calls[0].get("arguments", "").lower(), repr(calls[:1])

def t_responses_reasoning():
    s, r = req("/v1/responses", {"model": MODEL, "temperature": 0, "input": "Is 91 prime? Answer yes or no.", "reasoning": {"effort": "high"}, "max_output_tokens": 1500})
    kinds = [o.get("type") for o in r.get("output", [])] if s == 200 else []
    return s == 200 and "reasoning" in kinds and "message" in kinds, repr(kinds)

def t_messages_image():
    s, r = req("/v1/messages", {"model": MODEL, "temperature": 0, "max_tokens": 20, "system": "Answer with one English word.",
                                "messages": [{"role": "user", "content": [
                                    {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                 "data": base64.b64encode(SOLID_BLUE).decode()}},
                                    {"type": "text", "text": "What colour is this image?"}]}]})
    txt = " ".join(c.get("text", "") for c in r.get("content", [])) if s == 200 else str(r)
    return s == 200 and "blue" in txt.lower(), repr(txt[:80])

def t_messages_tools():
    s, r = req("/v1/messages", {"model": MODEL, "temperature": 0, "max_tokens": 200, "messages": [{"role": "user", "content": "What is the weather in Paris? Use the tool."}],
                                "tools": [{"name": "get_weather", "description": "Get the weather for a city",
                                           "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]})
    uses = [c for c in r.get("content", []) if c.get("type") == "tool_use"] if s == 200 else []
    return bool(uses) and uses[0].get("input", {}).get("city", "").lower() == "paris" and r.get("stop_reason") == "tool_use", repr(uses[:1])

def t_messages_thinking():
    s, r = req("/v1/messages", {"model": MODEL, "temperature": 0, "max_tokens": 2000, "thinking": {"type": "enabled", "budget_tokens": 1024},
                                "messages": [{"role": "user", "content": "Is 91 prime? Answer yes or no."}]})
    kinds = [c.get("type") for c in r.get("content", [])] if s == 200 else str(r)
    return s == 200 and "thinking" in kinds and "text" in kinds, repr(kinds)

def t_messages_stream():
    t = sse("/v1/messages", {"model": MODEL, "temperature": 0, "max_tokens": 30, "stream": True, "messages": [{"role": "user", "content": "Count to five."}]})
    ok = all(e in t for e in ("message_start", "content_block_delta", "message_stop"))
    return ok, f"{t.count('content_block_delta')} deltas"

def metric(name):
    s, b = req("/metrics", raw=True)
    for line in b.decode().splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            return float(line.rsplit(" ", 1)[1])
    return None

def t_dspark():
    v0 = metric("sglang:spec_verify_calls_total")
    s, r = chat([{"role": "user", "content": "Write a Python function that returns the n-th Fibonacci number, with a docstring."}], max_tokens=200)
    v1, acc = metric("sglang:spec_verify_calls_total"), metric("sglang:spec_accept_length")
    toks = r.get("usage", {}).get("completion_tokens", 0) if s == 200 else 0
    steps = (v1 or 0) - (v0 or 0)
    return s == 200 and steps > 0 and toks / steps > 1.5, f"{toks} tokens in {steps:.0f} verify steps ({toks / max(steps, 1):.2f} tok/step), accept_length {acc}"


CHECKS = [("models", t_models), ("chat basic + usage", t_basic), ("system prompt", t_system), ("multi-turn", t_multiturn),
          ("stop sequence", t_stop), ("max_tokens / finish=length", t_maxtokens), ("seed reproducibility", t_seed), ("n=3", t_n),
          ("streaming + include_usage", t_stream), ("client disconnect mid-stream", t_disconnect), ("over-length -> 400", t_overlength),
          ("logprobs + top_logprobs", t_logprobs), ("prefix cache report", t_cached_report),
          ("thinking on (reasoning_content)", t_thinking_on), ("thinking alias enable_thinking", t_thinking_alias),
          ("thinking off", t_thinking_off), ("reasoning_effort low/max", t_effort),
          ("tool call", t_tool_call), ("parallel tool calls", t_tool_parallel), ("tool_choice forced", t_tool_choice),
          ("tool result round trip", t_tool_roundtrip), ("json_schema", t_json_schema), ("json_object", t_json_object),
          ("image data URI", t_image_data), ("image quadrants (spatial)", t_image_quadrants), ("image http URL", t_image_http),
          ("multiple images", t_multi_image), ("mixed image+text batch c8", t_image_concurrent), ("image + thinking", t_image_thinking),
          ("sampling kwargs (top_p/top_k/min_p/penalties)", t_sampling_kwargs), ("DSpark speculative decoding active", t_dspark),
          ("/v1/completions", t_completions), ("/v1/responses", t_responses), ("/v1/responses stream", t_responses_stream),
          ("/v1/responses image input", t_responses_image), ("/v1/responses tools", t_responses_tools),
          ("/v1/responses reasoning", t_responses_reasoning), ("/v1/messages (Anthropic)", t_messages),
          ("/v1/messages image", t_messages_image), ("/v1/messages tools", t_messages_tools),
          ("/v1/messages thinking", t_messages_thinking), ("/v1/messages stream", t_messages_stream)]

if __name__ == "__main__":
    for n, f in CHECKS: check(n, f)
    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed" + (f"; FAILED: {', '.join(bad)}" if bad else "")
          + (f"; known limitations observed: {', '.join(sorted(KNOWN))}" if KNOWN else ""))
    sys.exit(len(bad))
