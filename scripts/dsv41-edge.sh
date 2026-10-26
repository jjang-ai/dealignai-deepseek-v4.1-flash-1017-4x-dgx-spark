#!/usr/bin/env bash
# dsv41-edge.sh start|stop|status — the keep-alive edge proxy (scripts/edge_proxy.py) for a tunnel or reverse proxy
# with a first-byte timeout (Cloudflare 100 s). Runs as container dsv41-edge from the engine image (it has aiohttp),
# host network, 127.0.0.1:EDGE_PORT -> the engine port; restarts with docker (survives reboots).
# Point the tunnel at http://localhost:EDGE_PORT instead of the engine port.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/cluster.env"
EDGE_PORT=${EDGE_PORT:-8890}
IMAGE=$(sed -n 's/^IMAGE=//p' "$HERE/config/env.tp4")
case "${1:-status}" in
  start)
    docker rm -f dsv41-edge >/dev/null 2>&1
    docker run -d --name dsv41-edge --restart unless-stopped --network host --no-healthcheck \
      -e UPSTREAM="http://127.0.0.1:$PORT" -e EDGE_HOST=127.0.0.1 -e EDGE_PORT="$EDGE_PORT" \
      -v "$HERE/scripts/edge_proxy.py:/edge_proxy.py:ro" --entrypoint /opt/sglang/bin/python3 "$IMAGE" /edge_proxy.py >/dev/null \
      && echo "edge proxy on 127.0.0.1:$EDGE_PORT -> :$PORT" ;;
  stop) docker rm -f dsv41-edge >/dev/null 2>&1 && echo "edge proxy stopped" ;;
  status) docker ps --filter name=dsv41-edge --format '{{.Names}} {{.Status}}' | grep . || echo "edge proxy not running" ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 1 ;;
esac
