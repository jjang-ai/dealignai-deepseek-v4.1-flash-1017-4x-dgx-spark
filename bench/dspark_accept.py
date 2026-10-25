"""DSpark acceptance per workload: per-request spec telemetry (return_spec_tokens_details), c1, 384 new tokens."""
import os, statistics, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench")); import bench
W = {
 "prose T=0": (bench.PROSE, 0.0, False), "code T=0": (bench.CODE, 0.0, False),
 "prose T=0.7": (bench.PROSE, 0.7, False), "code T=0.7": (bench.CODE, 0.7, False),
 "thinking T=0": (["How many integers between 1 and 1000 are divisible by 3 or 5 but not 15? Show reasoning."] * 3, 0.0, True),
 "json T=0": (["Return a JSON array of 15 objects with fields id, name, email, city for fictional users."] * 3, 0.0, False),
}
for name, (prompts, temp, think) in W.items():
    acc, tps = [], []
    for i, p in enumerate(prompts):
        body = {"model": bench.MODEL, "messages": [{"role": "user", "content": p}], "max_tokens": 1500 if think else 384,
                "temperature": temp, "chat_template_kwargs": {"thinking": think}, "return_spec_tokens_details": True}
        import time; t0 = time.time(); r = bench.post("/v1/chat/completions", body); dt = time.time() - t0
        u = r["usage"]; d = (r.get("sglext") or {}).get("spec_tokens_details") or {}
        if d.get("spec_verify_ct"): acc.append((d["spec_accept_length"], d["spec_accept_rate"]))
        tps.append(u["completion_tokens"] / dt)
    al = statistics.mean(a for a, _ in acc) if acc else float("nan"); ar = statistics.mean(b for _, b in acc) if acc else float("nan")
    print(f"{name:<14} tokens/step {al:4.2f} (of max 6)  draft accept rate {ar:4.2f}   c1 {statistics.median(tps):5.1f} tok/s", flush=True)
