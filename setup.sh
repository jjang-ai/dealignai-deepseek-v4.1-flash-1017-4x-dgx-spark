#!/usr/bin/env bash
# setup.sh — guided setup of the 4x DGX Spark ring for DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8. Run it on the
# Spark that will be the head. Safe to re-run at any point: every step checks first and skips what is done.
#
#   ./setup.sh           everything: checks -> cables -> install (kernel, fabric, NCCL, image, weights, Engram,
#                        mesh, hardening, engine) -> start -> API test
#   ./setup.sh check     only check the four nodes (ssh, sudo, GPU, kernel, memory, disk, Docker, packages,
#                        CX7 ports and links, internet, Hugging Face access) and the cables; changes nothing
#   ./setup.sh cables    only the cable check: shows which port reaches which node and how to fix wrong cabling
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
say(){ printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
die(){ printf '\n%sSTOP: %s%s\n' "$R" "$*" "$N" >&2; exit 1; }
MODE=${1:-all}
SSHO=(-n -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new)

# ---------------------------------------------------------------- 0. cluster.env
if [[ ! -f cluster.env ]]; then
  say "Describe your four Sparks"
  echo "Enter the LAN address (or hostname) of each Spark, separated by spaces, THIS one first."
  echo "The order of the other three does not matter: the cable check works out the ring order."
  read -r -p "> " -a ADDR
  [[ ${#ADDR[@]} == 4 ]] || die "need exactly 4 addresses, got ${#ADDR[@]}"
  sed -e "s|^NODE_LAN=.*|NODE_LAN=(${ADDR[*]})   # rank 0..3 in ring order (setup.sh fills this in)|" cluster.env.example > cluster.env
  echo "wrote cluster.env (edit it for a different model path, port or API key)"
fi
# shellcheck source=/dev/null
source cluster.env
U=$NODE_USER

# ---------------------------------------------------------------- 1. reach every node
say "1/6  ssh and sudo on every node"
bad=0
for ip in "${NODE_LAN[@]}"; do
  if ! ssh "${SSHO[@]}" "$U@$ip" true 2>/dev/null; then
    echo "  ${R}FAIL${N} $ip: no passwordless ssh as $U.  Fix (on this Spark):  ssh-keygen -t ed25519 (if you have no key);  ssh-copy-id $U@$ip"
    bad=1; continue
  fi
  if ! ssh "${SSHO[@]}" "$U@$ip" 'sudo -n true' 2>/dev/null; then
    echo "  ${R}FAIL${N} $ip: sudo asks for a password.  Fix (on $ip):  echo \"$U ALL=(ALL) NOPASSWD:ALL\" | sudo tee /etc/sudoers.d/dsv41"
    bad=1; continue
  fi
  echo "  ${G}OK${N}   $ip ($(ssh "${SSHO[@]}" "$U@$ip" hostname))"
done
[[ $bad == 0 ]] || die "fix the lines above, then run ./setup.sh again"
[[ $(ssh "${SSHO[@]}" "$U@${NODE_LAN[0]}" hostname) == "$(hostname)" ]] \
  || die "the first address in cluster.env (${NODE_LAN[0]}) is not this Spark: run setup.sh on the head, or put this Spark's address first"

# ---------------------------------------------------------------- 2. node checks
say "2/6  hardware, software, disk and links on every node"
read -r -d '' PROBE <<'EOS'
ok(){ echo "OK   $*"; }; warn(){ echo "WARN $*"; }; fail(){ echo "FAIL $*"; }
[ "$(uname -m)" = aarch64 ] && ok "arch aarch64" || fail "arch $(uname -m): this package is for DGX Spark (aarch64)"
g=$(nvidia-smi -L 2>/dev/null | head -1)
case "$g" in *GB10*) ok "GPU $g";; "") fail "nvidia-smi does not work: the NVIDIA driver is not loaded";; *) fail "GPU '$g' is not a GB10";; esac
k=$(uname -r); [ "$k" = "$KERNEL" ] && ok "kernel $k" || warn "kernel $k (not $KERNEL): ./setup.sh installs $KERNEL and asks for one reboot"
m=$(awk '/MemTotal/{print int($2/1048576)}' /proc/meminfo); [ "$m" -ge 115 ] && ok "memory ${m} GiB" || fail "memory ${m} GiB: a Spark has ~121 GiB"
need(){ d=$1; while [ ! -d "$d" ]; do d=$(dirname "$d"); done; df -BG --output=avail "$d" | tail -1 | tr -dc 0-9; }
have=$(ls "$MODEL_DIR"/model-*-of-00048.safetensors 2>/dev/null | wc -l)
want=$(( have >= 48 ? 0 : 520 )); eg=$(ls "$ENGRAM_DIR"/*.bin 2>/dev/null | wc -l); [ "$eg" -ge 2 ] || want=$((want + 50))
free=$(need "$MODEL_DIR")
[ "$free" -ge "$want" ] && ok "disk: ${free} GB free for the model/Engram (need ${want} GB; ${have}/48 shards present)" \
  || fail "disk: ${free} GB free under $MODEL_DIR, need ${want} GB (model 510 GB + Engram packs ~50 GB). Free space or set MODEL_DIR/ENGRAM_DIR in cluster.env to a bigger disk"
if docker info >/dev/null 2>&1; then
  dr=$(docker info -f '{{.DockerRootDir}}'); df_d=$(need "$dr")
  [ "$df_d" -ge 80 ] && ok "docker works, ${df_d} GB free in $dr" || fail "docker: only ${df_d} GB free in $dr, need 80 GB for the engine image"
  docker info 2>/dev/null | grep -qi nvidia && ok "NVIDIA container runtime" || fail "docker has no NVIDIA runtime: sudo apt-get install -y nvidia-container-toolkit && sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
else
  id -nG | grep -qw docker && fail "docker is not running: sudo systemctl enable --now docker" \
    || fail "user $(whoami) cannot use docker: sudo usermod -aG docker $(whoami), then log out and in again"
fi
miss=""
for c in git:git curl:curl rsync:rsync python3:python3 ethtool:ethtool nmcli:network-manager ibv_devinfo:ibverbs-utils ib_write_bw:perftest cc:build-essential tcpdump:tcpdump; do
  command -v "${c%%:*}" >/dev/null 2>&1 || miss="$miss ${c##*:}"
done
[ -z "$miss" ] && ok "tools present" || echo "PKGS$miss"
n=0; for i in enp1s0f0np0 enp1s0f1np1 enP2p1s0f0np0 enP2p1s0f1np1; do [ -e /sys/class/net/$i ] && n=$((n+1)); done
[ $n = 4 ] && ok "CX7: 4 fabric interfaces" || fail "CX7: only $n of the 4 fabric interfaces (enp1s0f0np0 enp1s0f1np1 enP2p1s0f0np0 enP2p1s0f1np1)"
r=$(ls /sys/class/infiniband 2>/dev/null | grep -c roce); [ "$r" -ge 4 ] && ok "RDMA: $r RoCE devices" || fail "RDMA: $r RoCE devices (need 4): check the mlx5 driver (lsmod | grep mlx5_ib)"
for i in enp1s0f0np0 enp1s0f1np1; do sudo ip link set dev $i up 2>/dev/null; done; sleep 3
for i in enp1s0f0np0 enp1s0f1np1; do
  c=$(cat /sys/class/net/$i/carrier 2>/dev/null); s=$(cat /sys/class/net/$i/speed 2>/dev/null)
  [ "$c" = 1 ] && ok "link $i up ${s} Mb/s" || fail "link $i DOWN: no cable, a loose cable, or the other end is not powered"
done
curl -sfI -m 10 https://huggingface.co >/dev/null && ok "internet (huggingface.co)" || warn "no internet: needed for packages, the engine build and the model download"
EOS
declare -A HOST
fails=0; pk=()
for r in 0 1 2 3; do
  ip=${NODE_LAN[$r]}
  out=$(ssh "${SSHO[@]:1}" "$U@$ip" "KERNEL='$KERNEL' MODEL_DIR='$MODEL_DIR' ENGRAM_DIR='$ENGRAM_DIR' bash -s" <<<"$PROBE" 2>&1)   # no -n: the probe comes on stdin
  HOST[$ip]=$(ssh "${SSHO[@]}" "$U@$ip" hostname)
  echo "  ${B}$ip (${HOST[$ip]})${N}"
  while IFS= read -r l; do
    case "$l" in
      OK*) echo "    ${G}${l:0:4}${N}${l:4}";;
      WARN*) echo "    ${Y}${l:0:4}${N}${l:4}";;
      FAIL*) echo "    ${R}${l:0:4}${N}${l:4}"; fails=$((fails+1));;
      PKGS*) pk+=("$ip:${l#PKGS }"); echo "    ${Y}MISS${N} packages:${l#PKGS}";;
    esac
  done <<<"$out"
done
[[ $(printf '%s\n' "${HOST[@]}" | sort -u | wc -l) == 4 ]] || { echo "  ${R}FAIL${N} two nodes have the same hostname: give each Spark its own (sudo hostnamectl set-hostname ...)"; fails=$((fails+1)); }
if [[ ${#pk[@]} -gt 0 ]]; then
  if [[ $MODE == check ]]; then echo "  missing packages: run ./setup.sh (installs them) or: sudo apt-get install -y <the names above>"
  else for e in "${pk[@]}"; do ip=${e%%:*}; echo "  installing on $ip:${e#*:}"; ssh "${SSHO[@]}" "$U@$ip" "sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ${e#*:} >/dev/null" || fails=$((fails+1)); done; fi
fi
if curl -sf -m 20 "https://huggingface.co/api/models/$HF_REPO/revision/$HF_REVISION" >/dev/null || \
   curl -sf -m 20 -H "Authorization: Bearer $(cat ~/.cache/huggingface/token 2>/dev/null)" "https://huggingface.co/api/models/$HF_REPO/revision/$HF_REVISION" >/dev/null; then
  echo "  ${G}OK${N}   model $HF_REPO @ ${HF_REVISION:0:8} reachable"
else echo "  ${R}FAIL${N} cannot read $HF_REPO from Hugging Face (offline, or gated: put a token in ~/.cache/huggingface/token)"; fails=$((fails+1)); fi
[[ $fails == 0 ]] || { [[ $MODE == check ]] && echo; die "$fails problem(s) above; fix them and run ./setup.sh again"; }

# ---------------------------------------------------------------- 3. cables
say "3/6  cables (which port reaches which node)"
T=$(mktemp -d)
for ip in "${NODE_LAN[@]}"; do scp -q fabric/lldp.py "$U@$ip:/tmp/dsv41-lldp.py"; done
for ip in "${NODE_LAN[@]}"; do ssh "${SSHO[@]}" "$U@$ip" 'sudo python3 -I /tmp/dsv41-lldp.py listen 7' > "$T/$ip" 2>/dev/null & done
sleep 2; for ip in "${NODE_LAN[@]}"; do ssh "${SSHO[@]}" "$U@$ip" 'sudo python3 -I /tmp/dsv41-lldp.py send' & done; wait
names=$(for ip in "${NODE_LAN[@]}"; do printf '%s=%s,' "${HOST[$ip]}" "$ip"; done)
cat "$T"/* | python3 fabric/cabling.py --head "${HOST[${NODE_LAN[0]}]}" --names "$names" | tee "$T/report"; rc=${PIPESTATUS[1]}
[[ $rc == 0 ]] || die "fix the cabling as described above (README.md has a diagram), then run ./setup.sh again"
order=$(awk '/^ORDER/{$1=""; print}' "$T/report"); new=()
for h in $order; do for ip in "${NODE_LAN[@]}"; do [[ ${HOST[$ip]} == "$h" ]] && new+=("$ip"); done; done
if [[ "${new[*]}" != "${NODE_LAN[*]}" ]]; then
  sed -i "s|^NODE_LAN=.*|NODE_LAN=(${new[*]})   # rank 0..3 in ring order (set by setup.sh from the cable check)|" cluster.env || die "could not update cluster.env"
  echo "  ring order written to cluster.env: ${new[*]}"
fi
rm -rf "$T"
[[ $MODE == cables || $MODE == check ]] && { echo; echo "${G}all checks passed${N}"; exit 0; }

# ---------------------------------------------------------------- 4. install
say "4/6  install (kernel, fabric, NCCL, engine image, model download, Engram, mesh, hardening, engine profile)"
echo "  takes ~1-2 h the first time (most of it the 510 GB download); re-running skips what is done"
./install.sh all; rc=$?
if [[ $rc == 2 ]]; then
  echo; echo "${Y}The kernel was installed on some nodes. Reboot ALL FOUR Sparks now (sudo reboot on each),${N}"
  echo "${Y}wait until they are back, then run ./setup.sh again: it continues from here.${N}"; exit 2
fi
[[ $rc == 0 ]] || die "install step failed (output above). Fix it and run ./setup.sh again; docs/RUNBOOK.md and AGENTS.md list the usual causes"

# ---------------------------------------------------------------- 5. start
say "5/6  start the engine"
./dsv41 start
AK=$(sed -n 's/^API_KEY=//p' engine/.env.tp4 | tr -d '"'); AUTH=(); [[ -n "$AK" ]] && AUTH=(-H "Authorization: Bearer $AK")
for i in $(seq 90); do curl -fsS -m 5 "${AUTH[@]}" "http://10.10.100.1:$PORT/v1/loads" >/dev/null 2>&1 && break; sleep 10; done
curl -fsS -m 5 "${AUTH[@]}" "http://10.10.100.1:$PORT/v1/loads" >/dev/null 2>&1 || die "the engine did not come up in 15 min: ./dsv41 logs (and docs/RUNBOOK.md)"

# ---------------------------------------------------------------- 6. test
say "6/6  API test"
./dsv41 test || die "some API checks failed (above)"
echo; echo "${G}${B}Done.${N}"; ./dsv41 url
echo "Day to day: ./dsv41 status | stop | start | logs | bench | validate"
