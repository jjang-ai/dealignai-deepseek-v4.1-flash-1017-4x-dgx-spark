# AGENTS.md — troubleshooting playbook for coding agents

You are helping someone run this repository: DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8 on four DGX Spark (GB10) nodes
cabled as a switchless ConnectX-7 ring, TP4. This file tells you how the system is laid out, which command answers
which question, and the known failures with their fixes. Work from evidence: run the check, read the output, then act.

## Rules

- **Ask before anything disruptive**: rebooting a node, `./dsv41 stop`, reinstalling the kernel, deleting files or images.
  The engine is shared by every client of the endpoint.
- **Never while the engine runs**: change `hairpin_queue_size`, stop `dsv41-mesh`, re-initialise a fabric interface,
  `pkill` mesh markers, move cables. Stop the engine first (`./dsv41 stop`).
- **Never set** `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` (NaN logits) or add `max_split_size_mb` to it (reserved
  memory runs away to 110 GiB and nodes stall until their watchdog reboots them). Never `OFFLOAD_MODE=ram`.
- **After a node died, read its evidence before re-running the same load**: `/var/log/dsv41-blackbox/YYYYMMDD.log` on that
  node and `journalctl -b -1`. Re-running blind can take the node down again.
- **Memory experiments** (any change to `MEM_FRACTION_STATIC`, `MAX_TOTAL_TOKENS`, chunk size, allocator settings, the
  prefill hook): test on one cold ~1M prompt first with `tests/mem_abort.sh` running, only then a long run.
- Do not paste API keys or Hugging Face tokens into logs, issues or commits.

## Layout

- Nodes: rank 0..3 = ssh aliases `rk1..rk4` (rk1 = head, runs the API). LAN addresses in `cluster.env` (`NODE_LAN`, ring order).
- Ring addressing: node IPs `10.10.100.1..4` on a dummy `ring0` interface; cable r / PCIe function f uses `10.10.(2r+f).0/24`.
- Each node: two CX7 ports, each exposed as two PCIe functions: port 0 = `enp1s0f0np0` + `enP2p1s0f0np0`,
  port 1 = `enp1s0f1np1` + `enP2p1s0f1np1`; RDMA devices `rocep1s0f0`, `roceP2p1s0f0`, `rocep1s0f1`, `roceP2p1s0f1`.
- Engine checkout: `engine/` (symlink, created by `install.sh engine`); profile `engine/.env.tp4` (generated from
  `config/env.tp4` + `cluster.env`; edit those, then `./install.sh engine`). Log: `engine/logs-tp4/dsv41.log`;
  workers: `docker logs dsv41-worker` on rk2-rk4. Head container `dsv41-head`.
- Guard service `dsv41-guard` (scripts/dsv41-guard.sh): waits for nodes, preflights, drops caches + compacts memory,
  starts the engine, probes `/v1/loads` every 30 s, restarts after 3 failures (max 6/h). Logs and failure
  diagnostics: `logs/guard/` (each failure gets a `fail-*/` folder with head/worker logs, dmesg and black-box tails).
- Per node: black box `/var/log/dsv41-blackbox/` (1 s: MemAvailable, PSI, temps, GPU power/clock/throttle, CX7 carrier,
  Xid lines; fsync'd, 7 days). Node clocks may differ (compare by epoch, not wall time).

## First commands

| question | command |
|---|---|
| is every node OK (ssh, sudo, GPU, kernel, disk, Docker, packages, CX7, links, internet)? | `./setup.sh check` |
| are the cables right, and in which order? | `./setup.sh cables` |
| is the engine up, how much memory is free? | `./dsv41 status` |
| is the fabric healthy (routes, GIDs, mesh, NCCL lib, image, weights, hardening)? | `./dsv41 preflight` (`--bw` adds RDMA bandwidth on all 8 links, ~40 s) |
| what did the engine say? | `./dsv41 logs 300`, `./dsv41 logs rk3 300` |
| does the API work? | `./dsv41 test` (42 checks) |
| is it as fast as it should be? | `./dsv41 bench` and compare with docs/RESULTS.md |
| what is using memory / disk on each node? | `./dsv41 mem`, `./dsv41 disk` |
| free the machines for other work | `./dsv41 unload` (and `./dsv41 load` afterwards) |
| reboot a node safely | `./dsv41 reboot rkN` (stops the engine first, starts it when the node is back) |
| collect everything for a bug report | `./dsv41 diag` |

## 1. Cabling and links

`./setup.sh cables` sends test frames out of every port (EtherType 0x88b6, harmless) and reports which port reaches which node.
- **"RING OK"**: done; the order is written to `cluster.env`.
- **"CABLES IN THE WRONG PORTS"**: a cable joins port 0 to port 0 (and another port 1 to port 1). The output names the
  node(s) whose two cables to swap. Ring rule: every node's port 0 goes to the next node's port 1.
- **"no cable seen" on a port**: unplugged, not seated (push until it clicks), a dead cable, or the far Spark is off.
  Check `cat /sys/class/net/enp1s0f0np0/carrier` (1 = link) on both ends.
- **"connected by two cables"**: two cables between the same pair; the ring needs each node cabled to two different nodes.
- Link speed should read `200000` (`/sys/class/net/<if>/speed`). A link that is up but slow in `preflight --bw`:
  reseat or swap that cable; a cable that logs corrected PHY errors but carries line rate is fine.
- TX ~12.8 Gb/s while RX is fine, and `LTR-` on the root ports: wrong kernel. `./install.sh kernel` (6.17.0-1032), reboot.

## 2. Fabric addressing and RoCE

| preflight / log symptom | cause | fix |
|---|---|---|
| `gid3=0000:0000` on all four ports of one node; boot fails with `NCCL error: invalid usage` | that node stayed up while neighbours rebooted; it lost its RoCEv2 IPv4 GIDs | `scripts/fabric-heal.sh` (the guard and `./dsv41 start` run it before every boot; it also restarts an inactive `dsv41-mesh`). By hand, on that node, per fabric interface: `a=$(ip -4 -o addr show <if> \| awk '{print $4}'); sudo ip addr del $a dev <if>; sudo ip addr add $a dev <if>`; preflight must pass |
| RoCEv2 GID at index 5, not 3 | NetworkManager stable-privacy IPv6 adds a second link-local | `nmcli con mod ring-<if> ipv6.addr-gen-mode eui64` (install.sh fabric does it) |
| `meshroute ... MISSING`; boot fails with `ibv_modify_qp(RTR): Connection timed out` on ranks next to a rebooted node | NetworkManager flushed the opposite-node /32 routes | `./install.sh mesh` (re-runs `fabric/mesh-routes-to-nm.sh`, which makes them persistent) |
| `markers` not 2 / `dsv41-mesh` inactive / `tc rules not in hardware` | mesh unit failed at boot | with the engine stopped: `sudo systemctl restart dsv41-mesh`, then preflight |
| ssh/TCP to the opposite node hangs, ping works | node route without `src=` | `./install.sh fabric` |
| `ring0` missing or not 3 node routes | ring0 profile not applied | `./install.sh fabric` |
| `NCCL lib` mismatch | NCCL build missing on a node | `./install.sh nccl` |
| `image mismatch` | nodes run different engine images | `./install.sh image` (builds once, loads the same image everywhere) |

## 3. Engine does not boot

| log symptom | cause | fix |
|---|---|---|
| `The memory capacity is unbalanced ... pre_model_load_memory` | a node holds a large page cache (after download/SHA/rsync); GB10 does not count it as free | `scripts/drop-caches.sh` (the guard runs it before every start) |
| `Multi-thread loading shards` ~50-70 s per shard on one node, NVMe idle, `kcompactd` at 100 % | that node's free memory is fragmented after many restarts | stop, `scripts/drop-caches.sh` (also compacts), start; boot is ~3-4 min |
| `TP rank 0 could finish the model loading, but there are other ranks that didn't` and a worker `Exited (247)` with no traceback | mixed kernel/driver versions across nodes | all nodes on 6.17.0-1032 + driver 580.178.04 (`./setup.sh check` shows each) |
| `shards N/48` or `engram packs: 0` in preflight | weights or Engram packs missing on that node | `./install.sh weights`, `./install.sh engram` |
| guard log `more than 6 restarts in an hour — holding` | a persistent failure | read the newest `logs/guard/fail-*/`, fix, then `sudo systemctl restart dsv41-guard` |
| health OK but `/v1/models` 401 | `API_KEY` is set | send `Authorization: Bearer <key>` |
| "API key is invalid" from an Anthropic-style client (Claude Code, Agent SDK); engine log `POST /v1/v1/messages?beta=true 401` | client sends `x-api-key`, which SGLang ignores; base URL ends in `/v1` | go through the edge proxy (maps `x-api-key` to Bearer, collapses `/v1/v1/`), or use `ANTHROPIC_AUTH_TOKEN` and a base URL without `/v1` |

## 4. A node went down

1. Wait 3 minutes: with `install.sh hardening` a kernel panic, oops or lockup reboots the node by itself (panic=10,
   hardware watchdog 30 s), and the guard restarts the engine when all four answer and preflight passes.
2. Read the node's black box around the time it died. The last lines decide:
   - MemAvailable near 0 and PSI memory high for a while: out of memory. Find what changed (profile, a test, another
     process using RAM on that node). The engine needs the node to itself.
   - Throttle bits set or temperatures climbing: cooling/airflow.
   - Xid / NVRM / mlx5 lines: driver or NIC; collect `sudo dmesg` and `journalctl -b -1`.
   - Everything normal to the last second, no Xid/oops/panic, journal ending ~90 s earlier: **instant power loss**
     (power brick, cable, outlet). Not fixable in software; swap the brick with another node's to confirm.
3. If it stays dark, it needs a physical power cycle.

## 5. Memory

- Steady state per node after boot: ~13-16 GiB MemAvailable idle; during long prefills it dips, normally to >= 2.5 GiB.
- The prefill hook logs `DSV41 prefill trim N: ... cached but free, kept ... working set` (first 3 and every 50th).
  Frequent trims are normal under mixed load.
- KV pool is pinned (`MAX_TOTAL_TOKENS=4259840`, boot log `max_total_num_tokens=4259840`). If the boot log shows less,
  the node had less free memory at boot (another process, page cache): drop caches and restart. Do not raise
  `MEM_FRACTION_STATIC` above 0.86 or `vm.watermark_scale_factor` (it shrinks what CUDA reports free).
- Anything else using GPU/RAM on a node (another model, a notebook) will starve the engine: `./dsv41 mem` lists the
  biggest other processes per node. To use the machines for something else, `./dsv41 unload` first.
- Disk: logs are rotated (`/etc/logrotate.d/dsv41`, hourly, 200 MB x 3 per log: container logs and the engine log),
  the black box keeps 7 days. `./dsv41 status` flags a node under 50 GB free.

## 6. Prefix cache: "why was my prompt not cached?"

- Check `usage.prompt_tokens_details.cached_tokens` in the response. Cached = the longest shared prefix with an earlier
  request, in whole 256-token pages.
- The prefix must be **byte-identical from the first token**: same system prompt, same tool definitions in the same
  order, same document text. Timestamps, request IDs or random values early in the prompt break it for everything after.
  Put the large stable part first (document), the changing part last (question).
- Thinking content and chat-template options change the prompt; keep `chat_template_kwargs` the same across turns.
- Capacity: four ~1M-token sessions (4.26M tokens). The least recently used session is evicted first when a new one
  needs the space; a revisit after eviction is a normal cold prefill (~190 s for 1M).
- Short prompts (< one 8192-token chunk) are cached after the request finishes; a 2nd visit hits.
- If revisits of recent long sessions miss: confirm `DSV41_SWA_EVICT_INTERNAL_FIRST=1`, `DSV41_SWA_KEEP_TOKENS=8704`,
  `DSV41_SWA_PROMPT_TAIL_KEEP=1024` and `--swa-prefix-tails 128` are in `engine/.env.tp4`, and that the boot log shows
  `[swa_evict_internal_first] armed`. `bench/bench.py lru --len 1000000 --sessions 5` is the reference test.

## 7. API usage

- Endpoints: `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/v1/messages` (Anthropic), `/v1/models`,
  `/v1/loads` (liveness), `/metrics`. Model name `DeepSeek-V4.1-Flash-1017-UNCENSORED` (`SERVED_MODEL_NAME`).
- The head listens on `0.0.0.0:PORT` (8888): LAN clients use `http://<head LAN address>:8888/v1`. Unreachable from the
  LAN but fine on the head: a host firewall (`sudo ufw status`) or the wrong address.
- Always send `max_tokens` (`max_output_tokens` on `/v1/responses`). A prompt longer than the 1,048,576 context is a clean
  HTTP 400.
- Thinking: chat `"chat_template_kwargs":{"thinking":true}` (alias `enable_thinking`) + `"reasoning_effort":"low|high|max"`;
  responses `"reasoning":{"effort":"high"}`; messages `"thinking":{"type":"enabled","budget_tokens":N}` with N >= 1024
  (smaller is a 400 by design). Reasoning comes back as `reasoning_content` / `reasoning` items / `thinking` blocks.
- Images: chat `image_url` (data URI or http URL), responses `input_image`, messages `image` with a base64 `source`.
- Tools: OpenAI `tools` + `tool_choice` (parallel calls work), responses `function` tools, messages `tools` with
  `input_schema`. JSON output: `response_format` `json_schema` / `json_object`.
- Sampling kwargs accepted: temperature, top_p, top_k, min_p, repetition_penalty, presence_penalty, frequency_penalty, stop,
  n, logprobs/top_logprobs. `seed` with temperature > 0 is not reproducible under DSpark (greedy is deterministic).
- API key: `./dsv41 key <key>` (writes `cluster.env`, regenerates the profile, restarts). Clients send
  `Authorization: Bearer <key>`; SGLang does not read `x-api-key` (the edge proxy converts it). Without the key: 401 on every API route; `/health` and
  `/metrics` stay open. The guard, `dsv41`, tests and examples read the key from `engine/.env.tp4` / `DSV41_API_KEY`.
- Model ids: the served name plus `DSV41_MODEL_ALIASES` (`DeepSeek-V4.1-Flash-1017`, `DeepSeek-V4.1-Flash`, `DeepSeek`,
  `deepseek`); boot log `DSV41 model aliases: ... (2 routes)`.
- Behind Cloudflare Tunnel: point the tunnel at the edge proxy (`./dsv41 edge start`, `http://localhost:8890`).
  | symptom | cause | fix |
  |---|---|---|
  | `524` after ~100-125 s on a long prompt or long non-streamed answer | proxy first-byte timeout; the engine sends nothing until the prefill/answer is done | route through the edge proxy |
  | `403` `error code: 1010` | Browser Integrity Check blocks the client's user agent (`Python-urllib`) | send a normal `User-Agent` (SDKs do), or disable BIC for the API host |
  | an HTML challenge page instead of JSON | security level / bot protection challenged the client's IP | lower the security level or skip it for the API hostname (a Cloudflare configuration rule) |
  | `413` | request body over the plan limit (100 MB on Free) | fewer/smaller images |
  | streams arrive in bursts | a buffering proxy in between; Cloudflare itself streams SSE unbuffered | check other proxies; `response_buffering` must be off |
- Video: no video input; `examples/video.py` cuts frames with ffmpeg and sends them as images (32 per request tested).
- Odd images (4K, panorama, 8x8, alpha, WebP) work; corrupt data is a clean 400 (`tests/multimodal_edge.py`).
- Speed is reported per request by tokens: measure tok/s as `usage.completion_tokens / elapsed`, never by counting stream
  chunks (one DSpark step carries several tokens).

## 8. Slower than expected

1. `./dsv41 bench` against docs/RESULTS.md (code c1 ~83 tok/s, c8 ~230; cold prefill ~6k tok/s at 32-128k).
2. `./dsv41 preflight --bw`: every link > 100 Gb/s both ways.
3. Black box during a bench: `thr` (throttle reasons) should be 0x0, SM clock ~2.4-2.5 GHz, GPU temperature sane.
4. Long prefills and chats compete by design: while a long prompt prefills, decoders get 8 rounds per prefill chunk
   (`--prefill-decode-interval 8`). Lower it to favour bulk prefill, raise it for chat latency (docs/PERFORMANCE.md §7).
5. Prose decodes slower than code by nature (DSpark accepts fewer drafted tokens on prose: ~2 vs ~4 per step).

## Reporting

Collect (no secrets): `./setup.sh check` output, `./dsv41 status`, `./dsv41 preflight`, the newest `logs/guard/fail-*/`
folder, `engine/logs-tp4/dsv41.log` around the failure, and for a dead node its black-box lines from the last minute
before it died plus `journalctl -b -1 | tail -200`.
