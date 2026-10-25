# DeepSeek-V4.1-Flash-1017 UNCENSORED FP8 on 4x DGX Spark — switchless ring, TP4

Serving package for **our model, [`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`](https://huggingface.co/dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8)**,
on four GB10 DGX Sparks cabled directly to each other (no switch). It is adapted and tuned specifically for this
checkpoint (rev `c19ca5ca`, 510 GB: MXFP4 routed experts, FP8 dense, 203 GB Engram n-gram tables, DSpark draft):
one OpenAI / Anthropic / Responses endpoint with 1M-token context, four resident 1M sessions in the prefix cache,
DSpark speculative decoding and the Engram tables served from NVMe.

## Against the other 4x Spark repos (their benchmark, run here)

Each repo publishes with its own benchmark, so we ran theirs against this endpoint. "Theirs" is their published
number with that same benchmark. Method and caveats: [docs/COMPARISON.md](docs/COMPARISON.md).

**Decode**

| benchmark | this repo (ring) | theirs |
|---|---:|---:|
| knapcio speedbench 0.3, 73 prompts, c1, geomean tok/s | **84.6** (95 % CI 80.0-89.5) | knapcio 87.5 (switched RoCE, official checkpoint) |
| colonel-otto's matrix harness, c1 per-stream tok/s | **138.5** | colonel-otto ring 126.7 / 132.0 |
| colonel-otto's matrix harness, c4 / c8 per-stream tok/s | **74.2** / 51.6 | colonel-otto ring 70.8 / 56.8 |

**Cold prefill, tok/s** (filler = a repeated phrase, as their benchmarks use; real = unique source/docs text)

| prompt | 4k | 16k | 32k | 64k | 128k | 262k | ~330-390k | ~1M |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **this repo, filler** | **5,730** | **6,210** | **6,550** | **6,520** | **6,640** | **6,460** | | |
| knapcio, synthetic (switched RoCE) | 4,798 | 5,976 | 6,196 | 6,241 | 6,291 | | | |
| colonel-otto ring, filler | | | ~5,420 | | ~5,390 | | | |
| **this repo, real text** | 3,987 | **5,956** | **5,967** | **6,022** | **6,256** | | **5,984** | **~5,150** |
| knapcio, varied text | | | | | | | 5,720 (388k) | |
| colonel-otto ring, real text | | | 4.4-4.8k | | 4.2-4.7k | | | |

Decode matches knapcio's engine within the error bar (we run that engine, on a ring, with the FP8 checkpoint).
Prefill is +4-6 % over knapcio's switched numbers and +12-49 % over the other ring bring-up. What this repository
changes, all measured on this ring:

| | before | here |
|---|---|---|
| revisit a 1M session after other long sessions arrived | ~210 s re-prefill | **2.6-3.1 s, 100 % cached**; 4 x 1M resident, the oldest rotates out |
| 2nd turn of a 6k chat (prefix reuse) | 0 % | **93.7 %** |
| lowest free memory per node, 30-min mixed soak | 0.8-1.7 GiB | **>= 2.7 GiB** (no per-chunk allocator churn) |
| chat latency next to back-to-back 200k prefills (p50 / p95) | 36.0 / 59.6 s | **14.4 / 20.9 s** |
| CX7 link rate on affected kernels | 12.8 Gb/s | **111.85 Gb/s** |
| boot on a node after many restarts | ~40 min (fragmented memory) | **~3 min** |

## Cabling (four QSFP112 DAC cables, no switch)

```
   ┌───────────┐ p0 ════════ cable 0 ════════ p1 ┌───────────┐
   │  rk1      │                                  │  rk2      │
   │  (head)   │                                  │           │
   └───────────┘                                  └───────────┘
        p1                                              p0
        ║ cable 3                              cable 1  ║
        p0                                              p1
   ┌───────────┐                                  ┌───────────┐
   │  rk4      │                                  │  rk3      │
   └───────────┘ p1 ════════ cable 2 ════════ p0 └───────────┘
```
- `p0` is the CX7 port with interfaces `enp1s0f0np0` / `enP2p1s0f0np0`; `p1` has `enp1s0f1np1` / `enP2p1s0f1np1`.
- Every node's **p0 goes to the next node's p1**: rk1→rk2→rk3→rk4→rk1. Opposite nodes (rk1↔rk3, rk2↔rk4) have no
  cable; their traffic is forwarded by the neighbours' NICs.
- The LAN / Wi-Fi port of each Spark is only for management; all model traffic stays on the ring.
- You do not need to know which Spark is which: `./setup.sh cables` finds the ring order itself and tells you exactly
  which cable to move if one is in the wrong port or the wrong Spark.

## Install

On the Spark that will be the head (same user on all four, ~600 GB free disk per node):
```bash
./setup.sh
```
It asks for the four LAN addresses (this Spark first), then:
1. checks every node: passwordless ssh and sudo (prints the exact fix), GB10 GPU and driver, kernel, memory, free disk
   for the model, Docker + NVIDIA runtime, required packages (installs missing ones), the four CX7 interfaces, RDMA
   devices, link state and speed, internet and Hugging Face access;
2. checks the cables and writes the ring order;
3. installs: kernel 6.17.0-1032 (asks for one reboot, then `./setup.sh` again continues), ring addressing, switchless
   NCCL, the engine image, the 510 GB model (each node downloads a quarter, then they copy over the ring and verify
   SHA-256), the per-rank Engram packs, the hardware-forwarded mesh, node hardening, the engine profile and its service;
4. starts the engine and runs the API test.

`./setup.sh check` only checks (changes nothing); `./install.sh <step>` re-runs one install step.

## Use

```bash
./dsv41 status         # engine, KV pool, nodes, free memory, endpoint URLs
./dsv41 url            # endpoint + a curl example
./dsv41 test           # API suite: 42 checks
./dsv41 logs           # head log (./dsv41 logs rk3 200 for a worker)
./dsv41 stop | start | restart | preflight | bench | validate
python3 examples/client.py --url http://<head>:8888 --image some.png   # thinking, stream, tools, JSON, vision, long documents
bash examples/curl.sh
```
- Endpoint `http://<head LAN address>:8888/v1` (the head listens on every interface) or `http://10.10.100.1:8888/v1` on
  the ring; model `DeepSeek-V4.1-Flash-1017-UNCENSORED`. Any OpenAI- or Anthropic-compatible client works.
- Before exposing the port beyond your LAN, set `API_KEY` in `cluster.env`, then `./install.sh engine && ./dsv41 restart`.
  Clients send `Authorization: Bearer <key>` (Anthropic SDKs: use `auth_token` / `ANTHROPIC_AUTH_TOKEN`, not `x-api-key`).
- Thinking: `"chat_template_kwargs":{"thinking":true}` + `"reasoning_effort":"low|high|max"` on chat;
  `"reasoning":{"effort":...}` on `/v1/responses`; `"thinking":{"type":"enabled","budget_tokens":>=1024}` on `/v1/messages`.
  Always send `max_tokens`.
- Long documents: put the document first and keep it unchanged, with the question after it. Follow-ups and revisits are
  then served from the prefix cache (`usage.prompt_tokens_details.cached_tokens` shows the hit).
- Troubleshooting: [docs/RUNBOOK.md](docs/RUNBOOK.md). For coding agents helping with a setup: [AGENTS.md](AGENTS.md).

## Measured (docs/RESULTS.md)

| | |
|---|---|
| Context | 1,048,576 tokens per request; 4 concurrent ~1.04M prompts: 4/4 needles, 5.0k tok/s aggregate |
| KV pool | pinned at 4,259,840 tokens: four resident ~1M sessions; 8 request slots (CUDA graphs bs 1-8) |
| Decode, real text, T=0 (aggregate tok/s) | code c1 83 · c4 171 · c8 231; prose c1 57 · c4 113 · c8 158 (DSpark k=5, ~3.6-4 tokens per verify step on code) |
| Stability (one run, fresh package build) | 5 x 1M LRU PASS; 30-min long-prefix soak 896/896; 30-min mixed soak (8 clients: chat, vision, 50-200k prompts, 600k revisits) 0 failures, 183/183 cache hits; 0 Xid / CUDA / NCCL errors; every node >= 3.5 GiB free |
| API (tests/api_suite.py, 42/42) | chat, `/v1/completions`, `/v1/responses` (stream, images, tools, reasoning), `/v1/messages` (stream, images, tools, thinking), streaming with usage, n, stop, logprobs, JSON schema, tool calls (parallel, forced, round trip), thinking on/off + `reasoning_effort`, sampling kwargs (top_p, top_k, min_p, repetition/presence/frequency penalties), images (base64, URL, several), prefix-cache report, DSpark active |

## Layout

```
setup.sh                           guided setup and checks (start here)
dsv41                              day-to-day control: status, start/stop, logs, test, bench, validate
install.sh, cluster.env.example    the install steps setup.sh runs, and the cluster description
AGENTS.md                          troubleshooting playbook for coding agents (cabling, fabric, boot, memory, cache, API)
docs/RUNBOOK.md                    start/stop, reboots, failure signatures and fixes
docs/FABRIC.md                     cabling, addressing, the kernel/LTR TX fix, NCCL, mesh
docs/PERFORMANCE.md                every speed and memory mechanism: how it works, how it is enabled, exactness
docs/RESULTS.md                    measurements           docs/COMPARISON.md   against the other repos
config/env.tp4                     the serving profile    config/*.txt         shard split, kernel packages, HF SHA256s
fabric/                            ring addressing, node IPs, route persistence, cable mapper + checker, ssh config
patches/                           engine patch, NCCL #2393 fence, flatten-base Dockerfile
scripts/                           ring-preflight, node-hardening, black box, guard service, drop-caches, weight download
bench/  tests/  examples/          benchmarks · validate.sh, soaks, api_suite, mem_abort · client.py, curl.sh
```

## Contact

eric@dealign.ai (dealign.ai)

## License

AGPL-3.0-or-later (LICENSE), the license of the engine this repository patches. Third-party components: NOTICE.

## Credits

This package deploys other people's work, used under their licenses:
- [knapcio/DeepSeek-V4.1-Flash-4x-DGX-Spark-TP4](https://github.com/knapcio/DeepSeek-V4.1-Flash-4x-DGX-Spark-TP4) by knapcio and its
  contributors (AGPL-3.0): the engine and most of its speed work; zurih and 0xSero for the work it builds on.
- [SGLang](https://github.com/sgl-project/sglang).
- [b12x / RoCEnante](https://github.com/local-inference-lab/b12x) (local-inference-lab) and rhys101's RoCEnante overlay.
- [sparkring](https://github.com/FujitsuPolycom/sparkring) (FujitsuPolycom): hardware-forwarded ring paths; rchmagos: RoCEnante on a
  four-node switchless ring.
- [alexellis/switchless-nccl](https://github.com/alexellis/switchless-nccl) and
  [ondigo-winder/SWITCHLESS-NCCL-RING-4-GB10](https://github.com/ondigo-winder/SWITCHLESS-NCCL-RING-4-GB10): switchless ring NCCL;
  Stanislav Bardyuk: the NCCL PR #2393 fence; [NVIDIA NCCL](https://github.com/NVIDIA/nccl).
- colonel-otto: the ring bring-up and the matrix benchmark harness used for comparison; knapcio's
  [speedbench](https://github.com/knapcio/speedbench).
- DeepSeek, for DeepSeek-V4.1-Flash.
