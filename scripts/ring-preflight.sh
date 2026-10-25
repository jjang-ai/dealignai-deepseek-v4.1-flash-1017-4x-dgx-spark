#!/usr/bin/env bash
# ring-preflight.sh — verify every node of the 4x Spark ring before booting the engine.
# Checks the failure modes seen in practice:
#   kernel/LTR (TX cap), fabric addressing/MTU, RoCEv2 GID index 3, ring0 node routes,
#   mesh /32 routes (flushed by NM on a neighbour reboot), tc rules in hardware, markers,
#   hairpin queue size, NCCL library, image, Engram packs, checkpoint shards.
# usage: ring-preflight.sh [--bw]   (--bw adds an ib_write_bw pass over all 8 links, ~40 s)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/cluster.env"
MESH=$WORK/ring-mesh
NCCL_SHA=fa3d6cea317cdc02   # /opt/nccl-switchless-4hca-fence (4hca + NCCL#2393 AArch64 CTS fence)
IMAGE=$(sed -n 's/^IMAGE=//p' "$HERE/config/env.tp4")
fail=0
ok(){ printf '  \033[32m[+]\033[0m %s\n' "$*"; }
bad(){ printf '  \033[31m[x]\033[0m %s\n' "$*"; fail=1; }

img_ref=$(docker images -q "$IMAGE")
[[ -n "$img_ref" ]] || { echo "image $IMAGE not found on this node (./install.sh image)"; exit 1; }
for k in 1 2 3 4; do
  echo "== rk$k"
  routes=$(grep -oE 'ip route add [0-9./]+' "$MESH/mesh-up-rk$k.sh" | awk '{print $4}' | tr '\n' ' ')
  out=$(ssh -o BatchMode=yes -o ConnectTimeout=5 rk$k "ROUTES='$routes' IMAGE='$IMAGE' MODEL_DIR='$MODEL_DIR' ENGRAM_DIR='$ENGRAM_DIR' bash -s" <<'EOS'
echo "kernel $(uname -r)"
for b in 0000:00:00.0 0002:00:00.0; do echo "ltr $b $(sudo -n lspci -s $b -vv 2>/dev/null | grep -A1 DevCtl2: | grep -oE 'LTR[+-]')"; done
for p in enp1s0f0np0:rocep1s0f0 enp1s0f1np1:rocep1s0f1 enP2p1s0f0np0:roceP2p1s0f0 enP2p1s0f1np1:roceP2p1s0f1; do
  n=${p%%:*}; d=${p##*:}; ip4=$(ip -4 -o addr show dev $n | awk '{print $4}' | head -1)
  g=$(cat /sys/class/infiniband/$d/ports/1/gids/3); t=$(cat /sys/class/infiniband/$d/ports/1/gid_attrs/types/3 2>/dev/null)
  echo "port $n $(cat /sys/class/net/$n/operstate) mtu=$(cat /sys/class/net/$n/mtu) ip=$ip4 gid3=${g: -9} $t hp=$(devlink dev param show pci/$(basename $(readlink -f /sys/class/infiniband/$d/device)) name hairpin_queue_size 2>/dev/null | grep -oE 'value [0-9]+' | head -1 | cut -d' ' -f2)"
done
echo "ring0 $(ip -4 -o addr show dev ring0 | awk '{print $4}') routes=$(ip -4 route | grep -c '^10.10.100.')"
for r in $ROUTES; do ip route show exact $r | grep -q . && echo "meshroute $r Y" || echo "meshroute $r MISSING"; done
echo "tc_in_hw $(for n in enp1s0f0np0 enp1s0f1np1 enP2p1s0f0np0 enP2p1s0f1np1; do tc -s filter show dev $n ingress 2>/dev/null; done | grep -c in_hw)"
echo "markers $(systemctl list-units 'dsv41-mesh-marker@*' --no-legend --state=running | wc -l) mesh=$(systemctl is-active dsv41-mesh)"
echo "nccl $(sha256sum /opt/nccl-switchless-4hca-fence/libnccl.so.2.30.7 2>/dev/null | cut -c1-16)"
echo "image $(docker images -q "$IMAGE")"
echo "engram $(ls "$ENGRAM_DIR"/*.bin 2>/dev/null | wc -l)"
echo "shards $(ls "$MODEL_DIR"/model-*-of-00048.safetensors 2>/dev/null | wc -l)"
echo "harden panic=$(sysctl -n kernel.panic) minfree=$(sysctl -n vm.min_free_kbytes) wd=$(systemctl show -p RuntimeWatchdogUSec --value) bb=$(systemctl is-active dsv41-blackbox)"
echo "gpu $(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>&1 | head -1) busy=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | wc -l)"
EOS
)
  [[ -z "$out" ]] && { bad "unreachable over the fabric"; continue; }
  kern=$(awk '/^kernel/{print $2}' <<<"$out")
  [[ $kern == "$KERNEL" ]] && ok "kernel $kern" || bad "kernel $kern, expected $KERNEL (TX-capped kernels: 6.17.0-1014, 7.0.0-1019)"
  grep -q 'LTR-' <<<"$out" && bad "root port LTR- (CX7 TX will cap at ~12.8 Gb/s)" || ok "root ports LTR+"
  while read -r _ n st mtu ip gid t1 t2 hp; do
    [[ $st == up && $mtu == mtu=9000 && $ip == ip=10.10.* && $gid != gid3=0000:0000 && "$t1 $t2" == "RoCE v2" && $hp == hp=8192 ]] \
      && ok "$n $ip ${gid} RoCEv2 hairpin 8192" || bad "$n $st $mtu $ip $gid $t1 $t2 $hp (gid3=0000:0000: see docs/RUNBOOK.md, lost RoCE GIDs)"
  done < <(grep '^port' <<<"$out")
  grep -q 'routes=3' <<<"$out" && ok "ring0 $(awk '/^ring0/{print $2}' <<<"$out") + 3 node routes" || bad "ring0: $(grep ^ring0 <<<"$out")"
  grep -qE '^harden panic=10 minfree=2097152 wd=30s bb=active' <<<"$out" && ok "crash hardening (panic reboot, hw watchdog, min_free 2G, black box)" \
    || bad "$(grep ^harden <<<"$out") — run scripts/node-hardening.sh on this node (./install.sh hardening)"
  grep -q 'meshroute.*MISSING' <<<"$out" && bad "mesh routes: $(grep meshroute <<<"$out" | tr '\n' ' ')" || ok "mesh /32 routes present"
  [[ $(awk '/^tc_in_hw/{print $2}' <<<"$out") -ge 2 ]] && ok "tc rules in hardware" || bad "tc rules not in hardware"
  grep -q 'markers 2 mesh=active' <<<"$out" && ok "markers 2, dsv41-mesh active" || bad "$(grep ^markers <<<"$out")"
  grep -q "nccl $NCCL_SHA" <<<"$out" && ok "ring NCCL 4hca" || bad "NCCL lib: $(grep ^nccl <<<"$out")"
  grep -qx "image $img_ref" <<<"$out" && ok "image $img_ref" || bad "image mismatch: $(grep ^image <<<"$out")"
  e=$(awk '/^engram/{print $2}' <<<"$out"); [[ $e -ge 2 ]] && ok "engram packs: $e" || bad "engram packs: $e"
  grep -q 'shards 48' <<<"$out" && ok "checkpoint 48/48" || bad "$(grep ^shards <<<"$out")"
  grep -q 'busy=0' <<<"$out" && ok "GPU idle ($(awk '/^gpu/{print $2}' <<<"$out"))" || echo "  [i] GPU busy: $(grep ^gpu <<<"$out")"
done

if [[ "${1:-}" == --bw ]]; then
  echo "== RDMA bandwidth, all 8 links, both directions"
  t(){ ssh rk$1 "timeout 20 ib_write_bw -d $2 -x 3 -p $6 -s 1048576 -D 2 -q 4 >/dev/null 2>&1" & sleep 1.5
       ssh rk$3 "timeout 20 ib_write_bw -d $4 -x 3 -p $6 -s 1048576 -D 2 -q 4 --report_gbits $5 2>&1 | awk '/^ 1048576/{print \$4}'"; wait; }
  for r in 0 1 2 3; do a=$((r+1)); b=$(( (r+1)%4 + 1 )); for f in 0 1; do
    [[ $f == 0 ]] && { sd=rocep1s0f0; rd=rocep1s0f1; } || { sd=roceP2p1s0f0; rd=roceP2p1s0f1; }; sn=$((2*r+f))
    x=$(t $b $rd $a $sd 10.10.$sn.2 1875$sn); y=$(t $a $sd $b $rd 10.10.$sn.1 1876$sn)
    awk -v x="$x" -v y="$y" 'BEGIN{exit !(x>100 && y>100)}' && ok "cable$r f$f rk$a<->rk$b $x / $y Gb/s" || bad "cable$r f$f rk$a<->rk$b $x / $y Gb/s"
  done; done 2>/dev/null
fi
[[ $fail == 0 ]] && echo "ring-preflight: PASS" || { echo "ring-preflight: FAIL"; exit 1; }
