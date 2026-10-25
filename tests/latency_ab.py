#!/usr/bin/env python3
"""Chat latency under continuous cold prefill (for scheduler A/Bs). Runs MINUTES (default 5).

  1 x background: cold 200k-token real-text prompts back to back (max_tokens 1) -> prefill tok/s
  4 x chat:       ~2k-token fresh prompt + 150 new tokens, thinking off, T=0 -> latency p50/p95, TTFT-ish
"""
import os, statistics, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench")); import bench
MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 5; TAG = sys.argv[2] if len(sys.argv) > 2 else ""
END = time.time() + MIN * 60
chat_lat, chat_tok, pre = [], [], []
lock = threading.Lock()

def background():
    i = 0
    while time.time() < END:
        i += 1; t = bench.text_tokens(200_000, 777_000 + i + int(time.time()))
        try:
            r, dt = bench.chat([{"role": "user", "content": t + "\nReply OK."}], 1)
            with lock: pre.append((r["usage"]["prompt_tokens"], dt))
        except Exception as e:
            print("bg err", e, flush=True)

def chat(w):
    i = 0
    while time.time() < END:
        i += 1
        try:
            r, dt = bench.chat([{"role": "user", "content": bench.text_tokens(2000, w * 100_000 + i + int(time.time())) + "\n\nSummarise in 3 sentences."}], 150,
                               chat_template_kwargs={"thinking": False})
            with lock: chat_lat.append(dt); chat_tok.append(r["usage"]["completion_tokens"] / dt)
        except Exception as e:
            print("chat err", e, flush=True)

ts = [threading.Thread(target=background)] + [threading.Thread(target=chat, args=(w,)) for w in range(4)]
time.sleep(0); [t.start() for t in ts]; [t.join() for t in ts]
s = sorted(chat_lat)
ptok = sum(p for p, _ in pre); pdt = sum(d for _, d in pre)
print(f"{TAG:<28} chat n={len(s):>3} p50 {statistics.median(s):5.1f}s p95 {s[int(.95*(len(s)-1))]:5.1f}s "
      f"(per-req tok/s {statistics.median(chat_tok):5.1f}) | background prefill {len(pre)} x 200k, {ptok/max(pdt,1e-9):6.0f} tok/s while serving")
