"""Multi-turn prefix reuse: each turn resends the whole conversation; report cached share per turn."""
import bench
for n, seed in ((6000, 31), (30000, 32)):
    doc = bench.text_tokens_exact(n, seed=seed)
    msgs = [{"role": "user", "content": doc + "\n\nWhat language is most of the code above written in? One word."}]
    for turn in range(1, 5):
        r, dt = bench.chat(msgs, 40)
        u = r["usage"]; c = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        print(f"{n:>6} turn{turn}: prompt {u['prompt_tokens']:>6} cached {c:>6} ({100*c/u['prompt_tokens']:5.1f}%) {dt:5.1f}s", flush=True)
        msgs += [{"role": "assistant", "content": r["choices"][0]["message"]["content"]},
                 {"role": "user", "content": f"Follow-up {turn}: name one function defined above."}]
