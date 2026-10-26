#!/usr/bin/env bash
# install.sh — deploy DeepSeek-V4.1-Flash-1017 UNCENSORED FP8 on a 4x DGX Spark switchless ring (TP4).
# Every step is idempotent and can be re-run. Run on rank 0 (the head). Requires passwordless sudo on all nodes.
#
#   ./install.sh cabling   # check the cables: which port reaches which node, ring order, how to fix (= ./setup.sh cables)
#   ./install.sh kernel    # put every node on $KERNEL + matching NVIDIA modules (REBOOT REQUIRED after)
#   ./install.sh fabric    # ring addressing, node IPs (ring0), forwarding, ssh aliases rk1..rk4
#   ./install.sh nccl      # build 4-HCA ring NCCL + NCCL#2393 fence, install on all nodes
#   ./install.sh weights   # parallel download across all nodes, all-to-all copy over the ring, SHA-256 verify
#   ./install.sh engram    # pack Engram rows per rank on the head, ship to each node's NVMe
#   ./install.sh image     # engine clone + our patch, flattened pinned base, build once, load on all nodes
#   ./install.sh mesh      # hardware-forwarded opposite-node RoCE paths (sparkring), boot units, NM routes
#   ./install.sh hardening # crash -> auto-reboot, hardware watchdog, min_free 2G, black-box recorder
#   ./install.sh engine    # write the serving profile, link the engine, install the guard service
#   ./install.sh all       # everything above in order (stops after `kernel` if a reboot is needed)
#   ./install.sh verify    # ring-preflight --bw, boot, smoke, API suite, quick bench
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/cluster.env" ]] || { echo "copy cluster.env.example to cluster.env and edit it" >&2; exit 1; }
# shellcheck source=/dev/null
source "$HERE/cluster.env"
log(){ printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }
rk(){ echo "rk$(( $1 + 1 ))"; }                      # rank -> ssh alias
lan(){ echo "${NODE_LAN[$1]}"; }
on(){ local r=$1; shift; ssh -o BatchMode=yes "$NODE_USER@$(lan "$r")" "$@"; }   # over the LAN (before fabric)
onf(){ local r=$1; shift; ssh -o BatchMode=yes "$(rk "$r")" "$@"; }               # over the fabric
ALL=(0 1 2 3)

cmd_cabling(){ exec "$HERE/setup.sh" cables; }   # cable map + ring order + exact fix (fabric/lldp.py, fabric/cabling.py)

cmd_kernel(){
  local need=0
  for r in "${ALL[@]}"; do
    k=$(on "$r" uname -r)
    if [[ "$k" == "$KERNEL" ]]; then log "rank $r already on $KERNEL"; continue; fi
    log "rank $r: $k -> installing $KERNEL (+ nvidia-580-open modules, driver to match)"
    scp -q "$HERE/config/kernel-1032-packages.txt" "$NODE_USER@$(lan "$r"):/tmp/pk.txt"
    on "$r" 'sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --allow-downgrades $(cat /tmp/pk.txt) \
      && sudo sed -i "s/^GRUB_DEFAULT=.*/GRUB_DEFAULT=saved/" /etc/default/grub && sudo update-grub \
      && sudo grub-set-default "Advanced options for DGX OS GNU/Linux>DGX OS GNU/Linux, with Linux '"$KERNEL"'"'
    need=1
  done
  [[ $need == 0 ]] || { log "REBOOT the updated nodes, then re-run ./install.sh all"; exit 2; }
}

cmd_fabric(){
  for r in "${ALL[@]}"; do
    scp -q "$HERE/fabric/ring-net.sh" "$HERE/fabric/ring0.sh" "$NODE_USER@$(lan "$r"):/tmp/"
    on "$r" "sudo bash /tmp/ring-net.sh $r && sudo bash /tmp/ring0.sh $r" | sed "s/^/  rank$r: /"
    on "$r" 'for i in enp1s0f0np0 enp1s0f1np1 enP2p1s0f0np0 enP2p1s0f1np1; do sudo nmcli con mod ring-$i ipv6.addr-gen-mode eui64 && sudo nmcli con up ring-$i >/dev/null; done'
  done
  # ssh aliases rk1..rk4 on every node; pin fabric host keys only when they match the LAN key
  for r in "${ALL[@]}"; do
    sed "s#@NODE_USER@#$NODE_USER#" "$HERE/fabric/ssh-config-ring.txt" \
      | on "$r" 'sed -i "/# --- spark ring/,/# --- end spark ring ---/d" ~/.ssh/config 2>/dev/null; cat >> ~/.ssh/config'
  done
  : > /tmp/kh_add
  for r in "${ALL[@]}"; do
    a=$(ssh-keyscan -T 5 -t ed25519 "10.10.100.$((r+1))" 2>/dev/null | awk '{print $3}')
    b=$(ssh-keyscan -T 5 -t ed25519 "$(lan "$r")" 2>/dev/null | awk '{print $3}')
    [[ -n "$a" && "$a" == "$b" ]] || die "rank $r: fabric host key does not match LAN host key"
    echo "10.10.100.$((r+1)) ssh-ed25519 $a" >> /tmp/kh_add
  done
  for r in "${ALL[@]}"; do on "$r" 'grep -v "^10\.10\.100\." ~/.ssh/known_hosts > ~/.ssh/kh.tmp || true; cat >> ~/.ssh/kh.tmp; mv ~/.ssh/kh.tmp ~/.ssh/known_hosts' < /tmp/kh_add; done
  for r in "${ALL[@]}"; do onf "$r" hostname; done
}

cmd_nccl(){
  local dst=/opt/nccl-switchless-4hca-fence
  if onf 0 "test -f $dst/libnccl.so.2.30.7"; then log "NCCL already at $dst"; else
    log "building NCCL 2.30.7 + switchless v0.0.1 + 4hca + #2393 fence on rk2"
    onf 1 'set -e; mkdir -p ~/nccl-build && cd ~/nccl-build
      [ -d ondigo ] || git clone -q https://github.com/ondigo-winder/SWITCHLESS-NCCL-RING-4-GB10.git ondigo
      [ -f nccl-2.30.7-1.tar.gz ] || curl -sfL -o nccl-2.30.7-1.tar.gz https://github.com/NVIDIA/nccl/archive/refs/tags/v2.30.7-1.tar.gz
      B=https://github.com/alexellis/switchless-nccl/releases/download/v0.0.1/nccl-2.30.7-switchless-hardened-sm121-linux-arm64.tar.gz
      [ -d bundle ] || { curl -sfL -o bundle.tgz $B; mkdir bundle; tar xzf bundle.tgz -C bundle; }'
    scp -q "$HERE/patches/nccl-2393-aarch64-cts-fence.patch" "$(rk 1):nccl-build/"
    onf 1 'cd ~/nccl-build && sed -e "9s#.*#HERE=\$HOME/nccl-build/ondigo#" \
      -e "s#^make -j#git apply --index \"\$HOME/nccl-build/nccl-2393-aarch64-cts-fence.patch\"\nmake -j#" ondigo/scripts/build.sh > build-fence.sh \
      && bash build-fence.sh $HOME/nccl-build/nccl-2.30.7-1.tar.gz $HOME/nccl-build/bundle/nccl-2.30.7-switchless-hardened-sm121-linux-arm64 $HOME/nccl-switchless-4hca-fence'
    onf 1 'tar -C ~ -cf - nccl-switchless-4hca-fence' > /tmp/nccl.tar
    for r in "${ALL[@]}"; do onf "$r" 'sudo tar -C /opt -xf -' < /tmp/nccl.tar; done
  fi
  for r in "${ALL[@]}"; do onf "$r" "sha256sum $dst/libnccl.so.2.30.7 | cut -c1-16"; done | sort | uniq -c
}

cmd_weights(){
  mkdir -p /tmp/dsv41dl
  curl -s "https://huggingface.co/api/models/$HF_REPO/tree/$HF_REVISION?recursive=1" | python3 -c '
import json,sys; [print(x["lfs"]["oid"]+"  "+x["path"]) for x in json.load(sys.stdin) if x.get("lfs")]' > "$HERE/config/SHA256SUMS.hf"
  for r in "${ALL[@]}"; do
    onf "$r" "mkdir -p ~/dsv41/dl"; scp -q "$HERE"/scripts/{dl.sh,pull.sh,shacheck.sh} "$HERE"/config/files_s{1,2,3,4}.txt "$HERE/config/SHA256SUMS.hf" "$(rk "$r"):dsv41/dl/"
  done
  log "downloading: each node fetches its share (config/files_sN.txt) in parallel"
  local E="HF_REPO=$HF_REPO MODEL_DIR=$MODEL_DIR"
  for r in "${ALL[@]}"; do onf "$r" "$E P=3 bash ~/dsv41/dl/dl.sh ~/dsv41/dl/files_s$((r+1)).txt $HF_REVISION > ~/dsv41/dl/dl.log 2>&1" & done; wait
  log "all-to-all copy over the ring"
  for r in "${ALL[@]}"; do onf "$r" "$E bash ~/dsv41/dl/pull.sh $((r+1)) 1 2 3 4 > ~/dsv41/dl/pull.log 2>&1" & done; wait
  for r in "${ALL[@]}"; do onf "$r" "$E bash ~/dsv41/dl/shacheck.sh; echo \"\$(hostname) \$(cat ~/dsv41/dl/sha.result)\""; done
}

cmd_engram(){
  [[ -d "$WORK/engine" ]] || die "run ./install.sh image first (pack_engram.py comes with the engine)"
  mkdir -p "$ENGRAM_DIR"
  for r in "${ALL[@]}"; do
    [[ -f "$ENGRAM_DIR/engram-l14-r${r}of4.bin" ]] || python3 "$WORK/engine/scripts/pack_engram.py" --model "$MODEL_DIR" --out "$ENGRAM_DIR" --rank "$r" --tp 4 &
  done; wait
  for r in 1 2 3; do onf "$r" "mkdir -p $ENGRAM_DIR"; rsync -a "$ENGRAM_DIR/engram-l1-r${r}of4.bin" "$ENGRAM_DIR/engram-l14-r${r}of4.bin" "$(rk "$r"):$ENGRAM_DIR/"; done
}

cmd_image(){
  mkdir -p "$WORK"
  if [[ ! -d "$WORK/engine/.git" ]]; then git clone -q "$ENGINE_REPO" "$WORK/engine"; fi
  git -C "$WORK/engine" checkout -q "$ENGINE_COMMIT"
  git -C "$WORK/engine" apply --check "$HERE/patches/knapcio-506fb10-dsv411017.patch" 2>/dev/null \
    && git -C "$WORK/engine" apply "$HERE/patches/knapcio-506fb10-dsv411017.patch"
  (cd "$WORK/engine" && bash scripts/fetch-sglang-canary.sh)
  docker pull -q "$BASE_IMAGE_DIGEST"; docker tag "$BASE_IMAGE_DIGEST" dsv41-base:knap25
  docker build -q -t dsv41-base:knap25-flat "$HERE/patches/flatten-base"      # one layer: overlayfs depth limit
  docker build -f "$WORK/engine/Dockerfile.canary-roce" --build-arg BASE_IMAGE=dsv41-base:knap25-flat --pull=false \
    -t dsv41-4x-spark:knap25s "$WORK/engine"
  for r in 1 2 3; do docker save dsv41-4x-spark:knap25s | onf "$r" docker load; done
  for r in "${ALL[@]}"; do onf "$r" 'docker images -q dsv41-4x-spark:knap25s'; done | sort | uniq -c
}

cmd_mesh(){
  [[ -d "$WORK/sparkring/.git" ]] || git clone -q https://github.com/FujitsuPolycom/sparkring.git "$WORK/sparkring"
  git -C "$WORK/sparkring" checkout -q "$SPARKRING_COMMIT"
  local src=$WORK/sparkring/spark_transport/fabric/cx7_hairpin_diagonal/native/mlx5_rdma_tx_rewrite_probe.c
  for r in "${ALL[@]}"; do
    scp -q "$src" "$(rk "$r"):/tmp/marker.c"
    onf "$r" 'dpkg -s libibverbs-dev >/dev/null 2>&1 || sudo apt-get install -y -qq libibverbs-dev >/dev/null; cc -O2 -Wall -Wextra /tmp/marker.c -o /tmp/mlx5-rdma-tx-rewrite-probe -libverbs -lmlx5 && sudo install -d /opt/dsv41-mesh/bin && sudo install -m 755 /tmp/mlx5-rdma-tx-rewrite-probe /opt/dsv41-mesh/bin/'
  done
  local M=$WORK/engine/scripts/ring_mesh
  python3 "$M/plan.py" --sparkring "$WORK/sparkring" --out "$WORK/ring-mesh" rk1 rk2 rk3 rk4
  for r in "${ALL[@]}"; do
    n=$((r+1)); scp -q "$WORK/ring-mesh/mesh-up-rk$n.sh" "$WORK/ring-mesh/mesh-down-rk$n.sh" "$M/hairpin.sh" "$M/dsv41-mesh.service" "$M/dsv41-mesh-marker@.service" "$HERE/fabric/mesh-routes-to-nm.sh" "rk$n:/tmp/"
    onf "$r" "sudo install -m 755 /tmp/mesh-up-rk$n.sh /opt/dsv41-mesh/mesh-up.sh && sudo install -m 755 /tmp/mesh-down-rk$n.sh /opt/dsv41-mesh/mesh-down.sh \
      && sudo install -m 755 /tmp/hairpin.sh /opt/dsv41-mesh/ && sudo install -m 644 /tmp/dsv41-mesh.service /tmp/dsv41-mesh-marker@.service /etc/systemd/system/ \
      && sudo systemctl daemon-reload && sudo systemctl enable --now dsv41-mesh && sudo bash /tmp/mesh-routes-to-nm.sh /tmp/mesh-up-rk$n.sh"
  done
  # plan again now that hairpin_queue_size is 8192 everywhere (raises the RoCE cap to 256 KB)
  python3 "$M/plan.py" --sparkring "$WORK/sparkring" --out "$WORK/ring-mesh" rk1 rk2 rk3 rk4 | grep -E "cap|ROCE_MAX"
}

cmd_hardening(){   # crash -> auto-reboot, hardware watchdog, uniform min_free, black-box recorder (scripts/node-hardening.sh)
  for r in "${ALL[@]}"; do
    scp -q "$HERE/scripts/node-hardening.sh" "$HERE/scripts/dsv41-blackbox.py" "$NODE_USER@$(lan "$r"):/tmp/"
    if [[ $r == 0 ]]; then on "$r" "sudo bash /tmp/node-hardening.sh $WORK/engine/logs-tp4"; else on "$r" 'sudo bash /tmp/node-hardening.sh'; fi
  done
}

cmd_engine(){
  sed -e "s#@NODE_USER@#$NODE_USER#g" -e "s#@HOME@#$HOME#g" -e "s#^MODEL_DIR=.*#MODEL_DIR=$MODEL_DIR#" -e "s#^ENGRAM_DIR=.*#ENGRAM_DIR=$ENGRAM_DIR#" -e "s#^WORKER_ENGRAM_DIR=.*#WORKER_ENGRAM_DIR=$ENGRAM_DIR#" \
      -e "s#^SERVED_MODEL_NAME=.*#SERVED_MODEL_NAME=$SERVED_MODEL_NAME#" -e "s#^PORT=.*#PORT=$PORT#" -e "s#^API_KEY=.*#API_KEY=${API_KEY:-}#" \
      "$HERE/config/env.tp4" > "$WORK/engine/.env.tp4"
  for r in 1 2 3; do onf "$r" "docker volume inspect dsv41-local-weights >/dev/null 2>&1 || docker volume create --driver local --opt type=none --opt device=$MODEL_DIR --opt o=bind dsv41-local-weights >/dev/null"; done
  ln -sfn "$WORK/engine" "$HERE/engine"
  sed -e "s#@NODE_USER@#$NODE_USER#" -e "s#@HOME@#$HOME#" -e "s#@REPO@#$HERE#" "$HERE/scripts/dsv41-guard.service" > /tmp/dsv41-guard.service
  sudo install -m 644 /tmp/dsv41-guard.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable dsv41-guard
  log "engine profile at $WORK/engine/.env.tp4; start with: sudo systemctl start dsv41-guard  (or cd engine && ./start-tp4.sh serve)"
}

cmd_verify(){
  "$HERE/scripts/ring-preflight.sh" --bw
  "$HERE/scripts/drop-caches.sh"            # page cache from big file I/O trips SGLang's TP memory-balance check on GB10
  (cd "$HERE/engine" && ./start-tp4.sh serve)
  "$HERE/dsv41" test
  "$HERE/dsv41" bench
}

cmd_all(){ cmd_kernel; cmd_fabric; cmd_nccl; cmd_image; cmd_weights; cmd_engram; cmd_mesh; cmd_hardening; cmd_engine; log "done — ./install.sh verify"; }

[[ $# -ge 1 ]] || { sed -n '2,18p' "$0"; exit 1; }
"cmd_$1"
