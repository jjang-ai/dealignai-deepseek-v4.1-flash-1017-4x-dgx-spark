# Runbook — DSV4.1-Flash-1017 UNCENSORED, TP4 on the Spark ring

All commands from rk1 (the head), in this repository. `engine/` is the patched engine checkout (`install.sh engine`), profile `engine/.env.tp4`.

## Start / stop / status

`./dsv41 status | start | stop | logs | preflight | test` covers the daily commands; `./setup.sh check` checks every node and the cables. By hand:

```bash
scripts/ring-preflight.sh                        # add --bw for the 16-direction RDMA check (~40 s)
sudo systemctl start dsv41-guard                  # normal operation: the guard preflights, starts and watches the engine
cd engine
./start-tp4.sh serve                              # by hand: workers first, then head; returns when smoke-tested
./start-tp4.sh status | logs | logs rk3 200 | smoke
./start-tp4.sh stop                               # always stop before powering off/rebooting a node
```
With the guard running, stop it first (`sudo systemctl stop dsv41-guard`), or it restarts the engine.
Engine log: `engine/logs-tp4/dsv41.log`. The boot is good when it shows
`RoCEnante ready: world=4 hcas=rocep1s0f0,rocep1s0f1,roceP2p1s0f0,roceP2p1s0f1 gid_index=3 max_size=262144`,
`[moe_b12x_next] armed`, `DSV41_L2_PREFETCH: RoCE collectives prefetch`, `DSV41 prefill trim hook installed`,
`max_total_num_tokens=4259840`, and `Ready`.

## Rebooting nodes

1. `sudo systemctl stop dsv41-guard; ./start-tp4.sh stop`
2. reboot any subset; `dsv41-mesh.service` re-applies hairpin 8192 + tc rules + markers at boot,
   NetworkManager restores addresses, ring0 node routes and the mesh /32 routes.
3. `scripts/ring-preflight.sh --bw`, then `sudo systemctl start dsv41-guard`.

## Known failure signatures

| Symptom | Cause | Fix |
|---|---|---|
| `ib_write_bw` / iperf3 TX ~12.8 Gb/s, RX fine; root ports `LTR-` | kernel 6.17.0-1014 / 7.0.0-1019 | boot 6.17.0-1032 (docs/FABRIC.md) |
| `RoCE all-reduce connect failed … ibv_modify_qp(RTR): Connection timed out` on ranks next to a rebooted node | NM flushed the mesh /32 routes | `fabric/mesh-routes-to-nm.sh` (done; preflight checks it) |
| `TP rank 0 could finish the model loading, but there are other ranks that didn't…` and a worker container `Exited (247)` with no traceback | ranks on driver 580.159.03 / kernel 1018 vanished during the fast load's memory squeeze (MemAvailable ~15 GB) | all nodes on 6.17.0-1032 + 580.178.04 |
| `The memory capacity is unbalanced ... pre_model_load_memory=...` at boot | one node holds a big page cache (after a download/SHA check/rsync); GB10's CUDA free-memory query does not count it as free | `scripts/drop-caches.sh` (the guard and `install.sh verify` run it before every start) |
| `IBV_WC_RETRY_EXC_ERR` towards one peer, `Port: 1 Link DOWN` on both functions facing it, that node gone from the LAN | the node itself died (power/thermal/hang); the engine can't survive a lost TP rank | with `install.sh hardening` a panic or freeze reboots it by itself (kernel.panic=10 + SBSA hardware watchdog 30 s) and the guard restarts the engine; if it stays dark, power-cycle it. Read `journalctl -b -1` and `/var/log/dsv41-blackbox/` (1 s samples up to the crash), `ring-preflight.sh --bw`, `./start-tp4.sh serve` (the guard retries on its own once the node is back) |
| a node went dark and its black box (`/var/log/dsv41-blackbox/`) is normal up to the last second: memory, PSI, temperatures, GPU power and throttle bits all unremarkable, no Xid/oops/panic, journal ends early | instant power loss (power brick, cable, outlet), not software; journald's unflushed buffer explains the journal ending ~90 s earlier | swap the node's power brick with another node's, reseat cables, try another outlet/circuit; a power cap would not help |
| one node's boot crawls in `Multi-thread loading shards` (~50-70 s per shard instead of ~1 s) while its NVMe is idle; `kcompactd` at 100 % CPU, `compact_stall`/`compact_fail` climbing in `/proc/vmstat` | that node's free memory is fragmented after many engine restarts (no free blocks of order >= 9 in `/proc/buddyinfo`); pinned slabs and GPU allocations stall in direct compaction | stop the engine, `scripts/drop-caches.sh` (drops caches and compacts every node; the guard and `install.sh verify` run it before each start), start again: boot to Ready ~3 min |
| a worker exits at boot with `NCCL error: invalid usage`; `ring-preflight.sh` shows `gid3=0000:0000` on all four ports of one node | that node stayed up while its ring neighbours rebooted; the link flaps dropped its RoCEv2 IPv4 GID entries | automatic: `scripts/fabric-heal.sh` runs before every guarded start. By hand, on that node, per CX7 port: `a=$(ip -4 -o addr show <if> \| awk '{print $4}'); sudo ip addr del $a dev <if>; sudo ip addr add $a dev <if>` (the GID table is rebuilt from the addresses); `ring-preflight.sh` must PASS, then `./start-tp4.sh stop; ./start-tp4.sh serve` |
| RoCEv2 GID at index 5 instead of 3 | NM stable-privacy IPv6 link-local adds a 2nd fe80 GID pair | `ipv6.addr-gen-mode eui64` on ring profiles (done) |
| TCP to the opposite node hangs, ping works | route without `src=` → reply goes to Wi-Fi | ring0 routes carry `src=10.10.100.N` (done) |
| several long (≥500k) sessions resident but revisits re-prefill | SGLang's SWA eviction drops whole sessions / their tail SWA | `DSV41_SWA_EVICT_INTERNAL_FIRST=1`, `DSV41_SWA_KEEP_TOKENS=8704`, `--swa-prefix-tails 128` (all in config/env.tp4; docs/PERFORMANCE.md) |

## Never

- change `hairpin_queue_size`, stop `dsv41-mesh`, or re-init a fabric function while the engine runs;
- `pkill` mesh markers (use `systemctl stop dsv41-mesh-marker@<dev>` with the engine down);
- set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` (NaN logits on prefills > 64 tokens) or add
  `max_split_size_mb` to it (prefill scratch is then not reused: reserved memory ran away to 110 GiB and
  nodes stalled in reclaim until their watchdog rebooted them);
- use `OFFLOAD_MODE=ram` (the Engram tables would evict the model).
