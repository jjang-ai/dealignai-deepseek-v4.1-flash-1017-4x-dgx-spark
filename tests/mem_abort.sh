#!/usr/bin/env bash
# mem_abort.sh — safety net for memory experiments. Every 2 s reads MemAvailable on all 4 nodes
# (their black-box files). Below SOFT GiB on any node it kills the test clients (bench.py,
# long_prefix_soak.py, mixed_soak.py, validate.sh); below HARD GiB it stops the engine.
# A node that runs out of unified memory stalls in reclaim for minutes and then reboots, so this
# trips long before that. Usage: nohup tests/mem_abort.sh [SOFT] [HARD] > logs/mem_abort.log &
SOFT=${1:-1.0}; HARD=${2:-0.5}
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/engine"
kill_clients(){
  pkill -f "[v]alidate.sh"
  for k in 1 2 3 4; do ssh -o ConnectTimeout=3 "rk$k" 'pkill -f "[b]ench.py|[m]ixed_soak.py|[l]ong_prefix_soak.py"' 2>/dev/null; done
}
tripped=0
while true; do
  for k in 1 2 3 4; do
    m=$(ssh -o ConnectTimeout=3 "rk$k" 'tail -1 /var/log/dsv41-blackbox/$(date +%Y%m%d).log' 2>/dev/null | awk '{print $3}')
    [ -z "$m" ] && continue
    if awk -v m="$m" -v h="$HARD" 'BEGIN{exit !(m<h)}'; then
      echo "$(date +%T) rk$k MemAvailable $m GiB < $HARD: stopping clients and engine"; kill_clients
      (cd "$ENGINE" && ./start-tp4.sh stop >/dev/null 2>&1); exit 1
    elif awk -v m="$m" -v s="$SOFT" 'BEGIN{exit !(m<s)}'; then
      [ $tripped = 0 ] && echo "$(date +%T) rk$k MemAvailable $m GiB < $SOFT: killing test clients"
      tripped=1; kill_clients
    fi
  done
  sleep 2
done
