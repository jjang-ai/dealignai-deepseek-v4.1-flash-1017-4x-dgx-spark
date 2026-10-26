#!/usr/bin/env bash
# fabric-heal.sh — repair the two fabric states a node can be left in after a NEIGHBOUR reboots, on all four nodes.
#   1. RoCEv2 IPv4 GID (index 3) missing on a fabric port: the link flap dropped it; re-adding the port's IPv4
#      address rebuilds it (otherwise the engine fails at boot with "NCCL error: invalid usage").
#   2. dsv41-mesh not active (hairpin queue + tc rules + markers): restart it.
# Only for a stopped engine (the guard and ./dsv41 start run it before the preflight). Prints what it fixed.
set -uo pipefail
for k in 1 2 3 4; do
  ssh -n -o BatchMode=yes -o ConnectTimeout=5 rk$k 'fixed=""
    for p in enp1s0f0np0:rocep1s0f0 enp1s0f1np1:rocep1s0f1 enP2p1s0f0np0:roceP2p1s0f0 enP2p1s0f1np1:roceP2p1s0f1; do
      n=${p%%:*}; d=${p##*:}; g=$(cat /sys/class/infiniband/$d/ports/1/gids/3 2>/dev/null)
      if [ "${g: -9}" = "0000:0000" ]; then
        a=$(ip -4 -o addr show dev $n | awk "{print \$4}" | head -1)
        [ -n "$a" ] && sudo ip addr del $a dev $n && sudo ip addr add $a dev $n && fixed="$fixed gid:$n"
      fi
    done
    systemctl is-active -q dsv41-mesh || { sudo systemctl restart dsv41-mesh && fixed="$fixed mesh"; }
    [ -n "$fixed" ] && echo "rk'$k' $(hostname): fixed$fixed"; true' 2>/dev/null || echo "rk$k: unreachable"
done
