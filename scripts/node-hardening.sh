#!/usr/bin/env bash
# node-hardening.sh — run ON a node (sudo). Makes a node crash recoverable and diagnosable,
# without limiting memory or speed. Idempotent.
#
#   1. a kernel panic / oops / soft or hard lockup reboots the node after 10 s
#      (the default kernel.panic=0 halts forever, until someone presses the power button)
#   2. systemd arms the SBSA hardware watchdog (RuntimeWatchdogSec=30): a fully frozen kernel
#      is reset by hardware; a reboot that hangs is forced after 2 min
#   3. vm.min_free_kbytes is the same 2 GiB on every node (the kernel's own reserve for atomic
#      and reclaim allocations; the engine profile was tuned with it, rk4 had 45 MB);
#      vm.watermark_scale_factor stays at the default: raising it lowers what GB10's CUDA reports
#      free at boot and shrank the KV pool from ~3.9M to 1.85M tokens (measured)
#   4. dsv41-blackbox.service: 1 s samples of memory, PSI, GPU temp/power/clock/throttle, CPU
#      temp, CX7 carrier and new Xid lines, fsync'd to /var/log/dsv41-blackbox (7 days), so the
#      last seconds before a crash survive it
#   5. logrotate for the engine's container logs and log file (copytruncate: works on a running engine).
#      Docker's json-file logs have no size cap by default; a log-opts cap only applies after a docker restart,
#      which would stop the engine, so rotation is done from outside.
# hung_task_panic stays 0: long blocking NCCL/driver waits are legitimate under 1M prefills.
# usage: node-hardening.sh [ENGINE_LOG_DIR]   (the head passes <engine>/logs-tp4)
set -euo pipefail
[ "$(id -u)" = 0 ] || exec sudo "$0" "$@"
HERE=$(cd "$(dirname "$0")" && pwd)

cat > /etc/sysctl.d/90-dsv41-hardening.conf <<'EOF'
# DSV41 ring: reboot instead of hanging after a kernel crash (scripts/node-hardening.sh)
kernel.panic = 10
kernel.panic_on_oops = 1
kernel.softlockup_panic = 1
kernel.hardlockup_panic = 1
vm.min_free_kbytes = 2097152
EOF
sysctl -e -q -p /etc/sysctl.d/90-dsv41-hardening.conf

mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/90-dsv41-watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=30
RebootWatchdogSec=2min
EOF
systemctl daemon-reexec

install -m 755 "$HERE/dsv41-blackbox.py" /usr/local/bin/dsv41-blackbox.py
cat > /etc/systemd/system/dsv41-blackbox.service <<'EOF'
[Unit]
Description=DSV41 node black-box recorder (1 s health samples, survive crashes)
After=nvidia-persistenced.service
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/dsv41-blackbox.py
Restart=always
RestartSec=5
Nice=10
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload; systemctl enable -q dsv41-blackbox; systemctl restart dsv41-blackbox

LOGDIR=${1:-}
block(){ echo "$1 {"; [ -n "${2:-}" ] && echo "  su $2"; printf '  %s\n' "size 200M" "rotate 3" compress missingok notifempty copytruncate; echo "}"; }
{
  block "/var/lib/docker/containers/*/*-json.log"
  [ -n "$LOGDIR" ] && [ -d "$LOGDIR" ] && block "$LOGDIR/*.log" "$(stat -c '%U %G' "$LOGDIR")"   # user-owned dir: rotate as its owner
} > /etc/logrotate.d/dsv41
logrotate -d /etc/logrotate.d/dsv41 >/dev/null 2>&1 || echo "warning: logrotate config check failed"
# hourly check, so a busy engine cannot outgrow the daily run
cat > /etc/cron.hourly/dsv41-logrotate <<'EOF2'
#!/bin/sh
exec /usr/sbin/logrotate /etc/logrotate.d/dsv41
EOF2
chmod 755 /etc/cron.hourly/dsv41-logrotate

echo "$(hostname): panic=$(sysctl -n kernel.panic) oops=$(sysctl -n kernel.panic_on_oops) soft=$(sysctl -n kernel.softlockup_panic)" \
     "hard=$(sysctl -n kernel.hardlockup_panic 2>/dev/null || echo n/a) min_free=$(sysctl -n vm.min_free_kbytes)" \
     "watchdog=$(systemctl show -p RuntimeWatchdogUSec --value) blackbox=$(systemctl is-active dsv41-blackbox) logrotate=$([ -f /etc/logrotate.d/dsv41 ] && echo on)"
