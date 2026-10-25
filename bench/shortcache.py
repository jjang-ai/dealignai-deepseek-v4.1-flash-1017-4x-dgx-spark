import bench, sys
tag = sys.argv[1] if len(sys.argv) > 1 else ""
for n, seed in ((600, 11), (1600, 12), (6000, 13), (20000, 14), (60000, 15)):
    txt = bench.text_tokens_exact(n, seed=seed)
    res = []
    for i in range(2):
        r, dt = bench.chat([{"role": "user", "content": txt + "\n\nReply OK."}], 4)
        u = r["usage"]; res.append(((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0, u["prompt_tokens"]))
    full = (res[1][1] // 256) * 256
    print(f"{tag} {n:>6}: 2nd visit cached {res[1][0]:>6} / {res[1][1]:>6} prompt ({'HIT' if res[1][0] >= full - 256 else 'MISS'})", flush=True)
