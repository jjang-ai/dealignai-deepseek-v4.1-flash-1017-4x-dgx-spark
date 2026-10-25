#!/usr/bin/env bash
# validate.sh — prove the running engine (no restart) on the workload it is sized for:
#   5 x 1M-token sessions: 4 fit the pinned KV pool, all 4 revisits must hit; the 5th evicts the oldest (LRU)
#   30-min long-prefix soak: 200k-950k prefixes + fresh suffixes, 1 and 4 streams (tests/long_prefix_soak.py)
#   30-min mixed soak: 8 clients, chat / vision / 50-200k prompts / 600k session revisits (tests/mixed_soak.py)
#   then engine error counts, kernel Xid counts and every node's lowest MemAvailable (black box).
# Clients run on rk3 so the head only serves. Run tests/mem_abort.sh alongside when trying new memory settings.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
L=$(ls -t "$HERE"/engine/logs-tp4/*.log | head -1); T0=$(date +%T); E0=$(date +%s)   # each node converts E0 to its own clock
D=/tmp/dsv41-tests
AK=$(sed -n 's/^API_KEY=//p' "$HERE/engine/.env.tp4" | tr -d '"'); E=${AK:+DSV41_API_KEY=$AK }   # passed to the clients
ssh rk3 "mkdir -p $D" && scp -q "$HERE/bench/bench.py" "$HERE/tests/long_prefix_soak.py" "$HERE/tests/mixed_soak.py" "rk3:$D/"
echo "start $T0 $(grep ^IMAGE "$HERE/engine/.env.tp4") $(grep -m1 -oE 'max_total_num_tokens=[0-9]+' "$L")"
echo "== LRU 5x1M"; ssh rk3 "cd $D && ${E}python3 bench.py lru --len 1000000 --sessions 5" 2>&1
echo "== long-prefix soak 30 min"; ssh rk3 "cd $D && ${E}python3 long_prefix_soak.py 30 --out soak.jsonl 2>&1 | tail -2"
echo "== mixed soak 30 min"; ssh rk3 "cd $D && ${E}python3 mixed_soak.py 30" 2>&1
echo "== engine errors: $(grep -cE 'Xid|NCCL WARN|CUDA error|EngineDead|out of memory' "$L") (tracebacks: $(grep -c Traceback "$L")) prefill trims: $(grep -c 'DSV41 prefill trim [0-9]' "$L") (first 3 and every 50th are logged)"
for k in 1 2 3 4; do ssh rk$k "echo rk$k kernel-xid: \$(sudo dmesg | grep -ciE 'xid|nvrm.*error') memavail_min: \$(sudo awk -v t=\$(date -d @$E0 +%T) '\$1>=t{print \$3}' /var/log/dsv41-blackbox/\$(date +%Y%m%d).log | sort -n | head -1) GiB"; done
echo VALIDATION-DONE
