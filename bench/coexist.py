"""Long sessions + short chats together: load 4 x ~1M sessions, then short-prompt repeats and multi-turn chats,
then revisit the 4 long sessions. Everything must hit on revisit (except the known first-visit single-chunk case)."""
import bench, subprocess, sys
docs = [bench.text_tokens_exact(1000000, seed=6000 + i) for i in range(4)]
def ask(i, tag):
    r, dt = bench.chat([{"role": "user", "content": docs[i] + "\n\nReply OK."}], 4)
    u = r["usage"]; c = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    print(f"  {tag:<8} long{i} prompt {u['prompt_tokens']:>8} cached {c:>8} {dt:6.1f}s", flush=True); return c / u["prompt_tokens"]
print("== load 4 long sessions", flush=True)
for i in range(4): ask(i, "cold")
print("== short chats + multi-turn with the long sessions resident", flush=True)
subprocess.run([sys.executable, "shortcache.py", "with-4x1M-resident"])
subprocess.run([sys.executable, "multiturn.py"])
print("== revisit long sessions", flush=True)
hits = [ask(i, "revisit") for i in range(4)]
print("long-session retention with short traffic:", "PASS" if all(h > 0.95 for h in hits) else "FAIL", [round(h, 3) for h in hits])
