#!/usr/bin/env bash
# dsv41-guard.sh — keep the TP4 engine up (run by dsv41-guard.service on rk1).
#
#   1. wait until all four nodes answer over the fabric and ring-preflight passes
#   2. if the API is not healthy, start the engine (./start-tp4.sh serve)
#   3. every 30 s probe /v1/loads; every 5 min /health_generate (a 1-token generation)
#   4. after 3 consecutive failures: save diagnostics, stop, re-run preflight, restart
#      (exponential backoff, at most 6 restarts per hour, then hold and wait for a human)
#
# State/logs: <repo>/logs/guard/   Disable: sudo systemctl disable --now dsv41-guard
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/cluster.env"
ENG=$HERE/engine             # symlink to the engine checkout (install.sh engine)
G=$HERE/logs/guard; mkdir -p "$G"
API=http://10.10.100.1:$PORT
AK=$(sed -n 's/^API_KEY=//p' "$ENG/.env.tp4" 2>/dev/null | tr -d '"')   # optional API key: health probes must send it
AUTH=(); [[ -n "$AK" ]] && AUTH=(-H "Authorization: Bearer $AK")
log(){ echo "$(date '+%F %T') $*" | tee -a "$G/guard.log"; }

# Liveness: /v1/loads round-trips to the scheduler (answers between prefill chunks, so a long
# prefill does not look like a hang). /health is a static 200 in this build and proves nothing.
healthy(){ curl -fsS -m 30 "${AUTH[@]}" "$API/v1/loads" >/dev/null 2>&1; }
# Deep check: /health_generate runs a 1-token generation when idle and, when requests are
# running, succeeds on any scheduler output — safe to call under load (no queueing behind prefill).
gen_ok(){ curl -fsS -m 120 "${AUTH[@]}" -o /dev/null "$API/health_generate"; }
wait_nodes(){
  local miss
  while miss=$(for k in 1 2 3 4; do ssh -o BatchMode=yes -o ConnectTimeout=4 rk$k true 2>/dev/null || printf 'rk%s ' $k; done); [ -n "$miss" ]; do
    log "waiting for nodes: ${miss}(a crashed node reboots itself in ~2-3 min: node-hardening.sh; if it stays dark, power-cycle it)"
    sleep 20; done
  "$HERE/scripts/fabric-heal.sh" 2>&1 | while read -r l; do log "fabric-heal: $l"; done   # GIDs/mesh after a neighbour reboot
  until "$HERE/scripts/ring-preflight.sh" > "$G/preflight.last" 2>&1; do
    log "preflight FAIL: $(grep -F '[x]' "$G/preflight.last" | head -3 | tr '\n' ' ')"; sleep 30; done
  log "preflight PASS"
}
diag(){
  local d="$G/fail-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$d"
  cp "$ENG/logs-tp4/dsv41.log" "$d/head.log" 2>/dev/null
  for k in 2 3 4; do ssh rk$k 'docker logs --tail 400 dsv41-worker 2>&1' > "$d/rk$k.log" 2>&1; done
  for k in 1 2 3 4; do ssh -o ConnectTimeout=5 rk$k 'free -g; sudo dmesg -T 2>/dev/null | tail -30; echo "-- blackbox (last 120 s)"; sudo tail -120 /var/log/dsv41-blackbox/$(date +%Y%m%d).log' > "$d/rk$k.sys" 2>&1; done
  log "diagnostics in $d"
}
start(){
  "$HERE/scripts/drop-caches.sh" >> "$G/guard.log" 2>&1   # GB10: page cache trips the TP memory-balance check
  log "starting engine"
  (cd "$ENG" && ./start-tp4.sh serve > "$G/serve-$(date +%Y%m%d-%H%M%S).log" 2>&1)
  healthy && log "engine Ready" || log "engine start returned but /v1/loads is not answering"
}

restarts=(); backoff=30
wait_nodes
healthy || start
fails=0; tick=0
while true; do
  sleep 30; tick=$((tick+1))
  if healthy && { [ $((tick % 10)) -ne 0 ] || gen_ok; }; then fails=0; backoff=30; continue; fi
  fails=$((fails+1)); log "health check failed ($fails/3)"
  [ $fails -lt 3 ] && continue
  now=$(date +%s); restarts=($(for t in "${restarts[@]}"; do [ $((now-t)) -lt 3600 ] && echo $t; done) $now)
  diag
  if [ ${#restarts[@]} -gt 6 ]; then log "more than 6 restarts in an hour — holding; fix and restart dsv41-guard"; sleep infinity; fi
  (cd "$ENG" && ./start-tp4.sh stop >> "$G/guard.log" 2>&1)
  log "backoff ${backoff}s"; sleep $backoff; backoff=$((backoff*2 > 600 ? 600 : backoff*2))
  wait_nodes; start; fails=0
done
