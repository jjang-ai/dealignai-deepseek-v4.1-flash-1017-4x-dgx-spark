# Fabric: 4x DGX Spark switchless ConnectX-7 ring

## Cabling (checked by `./setup.sh cables`: `fabric/lldp.py` sends raw EtherType 0x88b6 frames out of every port, `fabric/cabling.py` builds the ring and names any cable to move)

```
rk1 (head)  p0 ──cable0── p1  rk2
rk2         p0 ──cable1── p1  rk3
rk3         p0 ──cable2── p1  rk4
rk4         p0 ──cable3── p1  rk1
```
p0 = `enp1s0f0np0`/`enP2p1s0f0np0` (the two PCIe-domain functions of physical port 0), p1 = `enp1s0f1np1`/`enP2p1s0f1np1`.
This is exactly the orientation sparkring's planner requires (every f0 port cabled to the next node's f1).
TP rank = rk number − 1. Opposite pairs (rk1↔rk3, rk2↔rk4) have no cable.

## Addressing (`fabric/ring-net.sh <rank>`, NetworkManager profiles `ring-<ifname>`)

One /24 per cable per PCIe function: cable r, function f → `10.10.(2r+f).0/24`, the f0-port end is `.1`, the f1-port end `.2`.
MTU 9000, IPv4 only, IPv6 link-local in **EUI-64 mode** (NM's default stable-privacy address adds a second fe80 GID pair
and pushes the IPv4 RoCEv2 GID from index 3 to 5). ARP isolation per fabric netdev (`arp_ignore=1`, `arp_announce=2`,
`rp_filter=0`, `/etc/sysctl.d/60-spark-ring.conf`) because both functions of a physical port see every frame.

| node | enp1s0f0np0 | enP2p1s0f0np0 | enp1s0f1np1 | enP2p1s0f1np1 | ring0 |
|---|---|---|---|---|---|
| rk1 | 10.10.0.1 | 10.10.1.1 | 10.10.6.2 | 10.10.7.2 | 10.10.100.1 |
| rk2 | 10.10.2.1 | 10.10.3.1 | 10.10.0.2 | 10.10.1.2 | 10.10.100.2 |
| rk3 | 10.10.4.1 | 10.10.5.1 | 10.10.2.2 | 10.10.3.2 | 10.10.100.3 |
| rk4 | 10.10.6.1 | 10.10.7.1 | 10.10.4.2 | 10.10.5.2 | 10.10.100.4 |

## Node IPs over the fabric (`fabric/ring0.sh <rank>`)

In the measured setup the management NICs are 1 GbE USB (rk1) and **Wi-Fi** (rk2-4). SGLang's per-step CPU-group (Gloo) traffic and NCCL
bootstrap must not ride Wi-Fi, so each node has a dummy `ring0` with `10.10.100.<rank+1>/32`, NM routes to the other three
(neighbours direct, the opposite node through the clockwise neighbour, **`src=` pinned** to the node IP — without it the
kernel sources from the link address and the opposite node has no return route), `ip_forward=1` and
`ring-forward.service` (fabric↔fabric ACCEPT ahead of Docker's FORWARD DROP). SSH aliases `rk1..rk4` (fabric/ssh-config-ring.txt)
on every node; host keys were pinned only after matching the LAN keys. Engine: `HEAD_IP=10.10.100.1`, `GLOO/NCCL_SOCKET_IFNAME=ring0`.

## Kernel fix: CX7 TX capped at 12.8 Gb/s (the big one)

Nodes on 6.17.0-1014-nvidia and 7.0.0-1019-nvidia transmitted at ~12.8 Gb/s on every function — RDMA and TCP, ~13 Gb/s
per PCIe root aggregate — while receiving at line rate; nodes on 6.17.0-1018 ran 111.9 Gb/s both ways. No pause frames,
drops, PHY or congestion counters; identical firmware config, QoS, MRRS, IOMMU mode; a mlx5 driver reload did not help.
The discriminator: **root ports `0000:00:00.0` / `0002:00:00.0` show `LTR-` in `lspci -vv` DevCtl2 on the slow kernels,
`LTR+` on 1018/1032** (NIC DMA reads stall without LTR; posted writes don't). `setpci` cannot flip it (lockdown=integrity).

Fix: `6.17.0-1032-nvidia` + `linux-modules-nvidia-580-open-6.17.0-1032-nvidia` (pins driver 580.178.04), package set in
`config/kernel-1032-packages.txt`, GRUB `saved` default (`install.sh kernel`): 12.8 → 111.85 Gb/s. All four nodes run 1032
(1018 + driver 580.159.03 also has LTR+ but lost ranks under the fast load's memory squeeze, docs/RUNBOOK.md).
`scripts/ring-preflight.sh` checks the kernel and LTR on every node.

Measured after the fix (`ib_write_bw`, 1 MiB, 4 QPs): all 16 directions 111.84-111.86 Gb/s. cable0 negotiates 2x100G lanes
(the others 4x50G) and logs corrected PHY bits but carries line rate; reseat it if errors grow.

## NCCL

NCCL 2.30.7 + alexellis/switchless-nccl v0.0.1 (skip tree/PAT, listener GIDs, hardening) + ondigo-winder
`nccl-2.30.7-switchless-4hca.patch` (four listener GIDs → all four RDMA functions; all-reduce 105 → 186 Gb/s), built with
the ondigo `scripts/build.sh` (all four source-tree hashes verified), plus the NCCL#2393 AArch64 CTS-ordering fence
(`patches/nccl-2393-aarch64-cts-fence.patch`), installed at `/opt/nccl-switchless-4hca-fence` (`libnccl.so.2.30.7` sha256
`fa3d6cea…`) on every node by `install.sh nccl` and bind-mounted over the image's pip NCCL by the launcher.

## RoCEnante opposite-node mesh (sparkring `cx7_hairpin_diagonal` @ f16b5f4)

RoCEnante's one-shot collectives write to every peer directly; the opposite node is reached by tagging those RDMA packets
(UDP sport 65535 → EtherType 0x88b5, source marker `/opt/dsv41-mesh/bin/mlx5-rdma-tx-rewrite-probe`) and letting the
neighbour's NIC rewrite and hairpin them out of its other port (`skip_sw` tc flower rules, in hardware; the kernel forwards
nothing). Plan (`install.sh mesh`): `python3 <engine>/scripts/ring_mesh/plan.py --sparkring $WORK/sparkring --out $WORK/ring-mesh rk1 rk2 rk3 rk4`.
`dsv41-mesh.service` (enabled everywhere) sets `hairpin_queue_size` 8192 at boot (→ 256 KB RoCE cap) and runs `mesh-up.sh`.

Measured: 61 KB RDMA write rk1→rk3 through rk2's NIC 10.1 µs vs 8.4 µs to a direct neighbour; `IpForwDatagrams` flat.

**Gotcha fixed:** mesh-up adds its /32 opposite-node routes with `ip route`; NetworkManager flushes them when a link bounces
(e.g. a neighbour reboots), and RoCEnante then dies at boot with `ibv_modify_qp(RTR): Connection timed out` on the ranks
next to the rebooted node. `fabric/mesh-routes-to-nm.sh` copies those routes into the NM ring profiles so they survive.

Never stop the mesh, re-init a fabric function or change the hairpin size while the engine runs.
