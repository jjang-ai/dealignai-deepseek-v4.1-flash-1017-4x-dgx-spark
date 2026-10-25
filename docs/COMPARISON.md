# How this deployment differs from the upstream repos, and what it measures against them

Three kinds of numbers appear below, and only the first two are comparisons:

1. **Same harness, measured here**: their benchmark tool run against this deployment, next to the number they publish
   with that tool.
2. **Same cluster, measured here**: an upstream engine line run on this exact ring with our bench, next to ours.
3. **Claimed**: numbers as each repo publishes them. Different harnesses, prompts, fabrics and checkpoints; listed for
   context only. Do not compare across rows.

Compared: [knapcio/DeepSeek-V4.1-Flash-4x-DGX-Spark-TP4](https://github.com/knapcio/DeepSeek-V4.1-Flash-4x-DGX-Spark-TP4)
(**this deployment runs knapcio's engine** at `506fb10` plus our patch), colonel-otto's 4x-dgx-spark-ring (a
switchless-ring DSV4.1 bring-up), and the MiaAI-Lab/DeepSeek-v4.1-Flash-DGX-Sparks production line as a baseline.

## 1. Same harness, measured here

### knapcio's speedbench 0.3 (73 frozen prompts, c1, T=0, 256 tokens, thinking off)

speedbench 0.3 at `e95a638` (knapcio cites `6733653`, which is not in the public history; both are tagged 0.3).
Geometric mean tok/s per category.

| | prose | code | structured | agentic | multilingual | long ctx | **all 73** | median TTFT |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| knapcio, published (switched RoCE, official 1017 checkpoint) | 64.7 | 106.8 | 109.5 | 98.3 | 70.0 | 91.0 | **87.5** | 205 ms |
| **this deployment** (switchless ring, UNCENSORED-FP8 checkpoint) | 64.4 | 104.3 | 102.9 | 92.8 | 67.2 | 88.3 | **84.6** [80.0, 89.5] | 216 ms |

Equal within the 95 % interval. knapcio's own ring-vs-switch table (their docs/switchless-ring.md) puts the ring
~6 % below the switch on sparkDash prose c1 (80.9 vs 86.5), so on equal fabric this is at or above their line.
**Decode speed is inherited, not improved here.**

### Matrix harness (colonel-otto's `bench-miaai.py`: unique cold prefix, temp 0.6, 128 tokens, "numbered words")

Per-stream decode after the first token (median of 5 trials); TTFT = cold prefill of the given prompt.

| | c1 | c4 per stream | c8 per stream | TTFT 8K | TTFT 32K | TTFT 131K |
|---|---:|---:|---:|---:|---:|---:|
| colonel-otto ring, published (RoCEnante mesh) | 126.7 / 132.0 | 70.8 / 69.7 | 56.8 / 51.4 | 1.56 s | 6.04 s | 24.3 s |
| **this deployment** | **138.5** | **74.2** | 51.6 | **1.39 s** | **5.0 s** | **19.7 s** |

The "numbered words" output is highly predictable, so DSpark accepts most drafts: this harness reads ~1.6x higher
than real prose (the MiaAI-Lab line gives 54.7 tok/s prose c1 on our bench vs 87.7 published with sparkDash).

### Cold prefill at the published prompt sizes (tok/s)

Two prompt kinds, because the repos publish both: *filler* (a short repeated phrase: hot Engram rows, the kind
sparkDash and `bench-miaai.py` use) and *real text* (unique source code + docs, never cached; `bench/bench.py`).
Filler rows here are prompt tokens / TTFT from `bench-miaai.py` (median of 3).

| prompt tokens | 4k | 8k | 16k | 32k | 64k | 128k | 262k | ~330-390k | ~1M |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **this deployment, filler** | **5,730** | **5,890** | **6,210** | **6,550** | **6,520** | **6,640** | **6,460** | | |
| knapcio published, synthetic (sparkDash, switched RoCE) | 4,798 | | 5,976 | 6,196 | 6,241 | 6,291 | | | |
| colonel-otto ring published, filler (`bench-miaai.py`) | | ~5,250 | | ~5,420 | | ~5,390 | | | |
| **this deployment, real text** | 3,987 | | **5,956** | **5,967** | **6,022** | **6,256** | | **5,984** (330k) | **~5,150** (190-195 s) |
| knapcio published, varied text | | | | | | | | 5,720 (388k) | |
| colonel-otto ring published, real text | | | | 4.4-4.8k (28k) | | 4.2-4.7k (103-114k) | | | |

On filler the ring deployment is +4-6 % over knapcio's switched numbers from 16k up and +19 % at 4k, and +12-23 %
over colonel-otto's ring; on real text it is +24-49 % over colonel-otto's ring and +5 % over knapcio's 388k figure.
Prefill sequence parallel and the chunked indexer are knapcio's; this repository adds the prefill memory trim (no
per-chunk driver calls, scratch reused across chunks), 8192-token prefill chunks (knapcio's example profile uses 4096) with the 1 GiB indexer budget, and a ring fabric at the full
111.85 Gb/s on every link (kernel/LTR fix).

## 2. Same cluster, measured here (bench/bench.py: real text, T=0, 256 tokens)

MiaAI-Lab production line @ `64b02de` (image `canary-roce`, chunk 4096, 16 slots, fraction 0.80) run on this ring
before switching to knapcio's engine, versus the final deployment:

| | MiaAI-Lab line | this deployment | change |
|---|---:|---:|---:|
| decode prose c1 / c8 | 54.7 / 153.7 | 57.0 / 157.9 | +4 % / +3 % |
| decode code c1 / c8 | 79.7 / 223.3 | 83.4 / 230.8 | +5 % / +3 % |
| cold prefill, real text ~120-130k | 4,612 tok/s (132k) | 6,069 tok/s (121k) | +32 % |
| cold prefill, real text ~250-340k | 4,684 tok/s (253k) | 5,908 tok/s (342k) | +26 % |
| 4 concurrent ~1M prompts | ~2.6k tok/s aggregate at depth | 5,031 tok/s aggregate, 4/4 needles | ~1.9x |
| KV pool | 3.85M tokens (floating) | 4,259,840 (pinned) | four 1M sessions always fit |
| 1M session revisit | re-prefill (~210 s) once other long sessions arrive | 2.6-3.0 s, 100 % cached | ~70x |
| decode c16 | 209.2 prose / 323.1 code | not served (8 slots) | traded for KV pool |

Most of the decode/prefill gain here is knapcio's engine work (b12x_next MoE, prefill sequence parallel, L2 prefetch,
draft-head fp8, verify cap); the session-cache, memory and stability rows are this repository's changes (section 4).

## 3. Claimed numbers, as published (not comparable with each other)

| repo | fabric | checkpoint | harness | prose c1 | code c1 | prefill |
|---|---|---|---|---:|---:|---|
| MiaAI-Lab | switched RoCE | official | sparkDash 1.8.8 | 87.7 | 124.8 | 4.9-5.0k real text 16-128k |
| knapcio | switched RoCE | official 1017 | sparkDash 1.8.8 | 91.3 | 142.9 | 6.0-6.3k at 16-128k (synthetic) |
| knapcio | switchless ring | official 1017 | sparkDash 1.8.8 | 80.9 | 120.5 | — |
| colonel-otto | switchless ring | official | bench-miaai | 129 (c1, all types) | — | ~5.4k (filler), 4.2-4.8k real |
| **this deployment** | switchless ring | UNCENSORED-FP8 | bench.py real text | 57.0 | 83.4 | 5.7-6.1k real text 22-514k |

Why they differ: sparkDash and bench-miaai prompts produce predictable text (high draft acceptance); synthetic or
repeated filler prefill hits hot Engram rows; switched fabrics avoid the ring's extra hop; 16-slot profiles add a c16 tier.

## 4. What this repository changes (measured effect)

| change | where | effect (measured on this ring) |
|---|---|---|
| **Long-session prefix retention**: SWA eviction tombstones internal radix nodes (oldest first) before any session's leaf | patch: `adapter/swa_evict_internal_first.py` (`DSV41_SWA_EVICT_INTERNAL_FIRST`, `DSV41_SWA_KEEP_TOKENS`) | 3 x 1M sessions, revisit: 208-213 s re-prefill → 2.3-2.7 s (≥ 99.7 % cached); 5 x 1M: the 4 most recent stay cached, the oldest rotates out |
| **Short-prompt prefix reuse**: keep the last 1024 prompt tokens' SWA through decode | patch: same adapter (`DSV41_SWA_PROMPT_TAIL_KEEP`) | 6k prompt 2nd visit 0 → full hit; 6k chat turn 2 0 % → 93.7 % reuse |
| **Prefill memory trim**: scratch stays cached between chunks; cached-but-free memory beyond one chunk's working set is returned at most every 10 s | patch: `adapter/prefill_empty_cache.py` | lowest MemAvailable under the 30-min mixed soak 0.8-1.7 GiB (earlier designs) → ≥ 2 GiB; no per-chunk driver calls; cold 1M unchanged |
| **Pinned KV pool + memory profile**: `MAX_TOTAL_TOKENS=4259840`, fraction 0.86, indexer logits budget 1 GiB, Engram cache 2 GiB, 8 slots | config/env.tp4 | four 1M sessions on every boot (unpinned: 3.9-4.59M, a 4th 1M session sometimes did not fit) |
| **Mixed-load scheduling**: `--prefill-decode-interval 8 --prefill-decode-interval-min-bs 1`, bypass 4096 | config/env.tp4 | chat p50 36.0 → 14.4 s, p95 59.6 → 20.9 s next to back-to-back cold 200k prompts (knapcio default: interval 2, bypass 256); long prefill slows to 3.1k tok/s only while chats run |
| **Ring fabric**: kernel 6.17.0-1032 (PCIe LTR) diagnosis, NetworkManager persistence of mesh routes, 4-HCA NCCL + NCCL#2393 fence built and installed by script | `install.sh kernel/fabric/nccl/mesh`, docs/FABRIC.md | CX7 TX 12.8 → 111.85 Gb/s on affected kernels; no RoCE connect failures after a neighbour reboot |
| **Node hardening and operations**: panic/lockup reboot, hardware watchdog, uniform min_free, 1 s fsync'd black box, guard service, ring preflight, cache drop + memory compaction before each start | scripts/, `install.sh hardening/engine` | a fragmented node's boot 40 min → 3 min; crashes self-recover and leave evidence; power-loss vs software crashes are distinguishable |
| **Proof tooling**: `tests/validate.sh` (5 x 1M LRU, long-prefix soak, mixed soak, error and memory minima), `tests/mem_abort.sh` | tests/ | the validation in docs/RESULTS.md in one command |
| **Checkpoint**: dealignai UNCENSORED-FP8 with pinned revision, per-node parallel download + ring all-to-all copy + SHA-256 check | `install.sh weights` | 510 GB on all four nodes in one step |

## 5. Inherited (credit where the speed comes from)

From knapcio / SGLang / b12x / sparkring, used unchanged: RoCEnante one-shot collectives and the
hardware-forwarded opposite-node paths, EP=1 expert tensor-parallel, b12x_next routed MoE, MXFP8 dense on b12x,
wo_a fp8 twin, replicated-linear split, fused hc stats, DSpark k=5 with verify cap and block verification, folded
sampling + fence, prefill sequence parallel, prefill TP split, the chunked indexer, L2 prefetch, the fast loader,
the Engram NVMe row store with prefetch and keepalive. Details: docs/PERFORMANCE.md.

## 6. Where this deployment is not better

- Decode tok/s equals knapcio's line (section 1); no decode speedup is claimed.
- No c16 tier: 8 slots keep the KV pool at four 1M sessions.
- While chats run, a long cold prefill gets less of the GPU (3.1k vs 5.0k tok/s), by design; lower the interval to favour bulk prefill.
- `seed` at T > 0 is not reproducible under DSpark (greedy is deterministic).

## Reproduce

```bash
python3 -m speedbench run --url http://10.10.100.1:8888 --label mine --out mine.json   # knapcio/speedbench, from rk3
python3 bench-miaai.py --base-url http://10.10.100.1:8888/v1 --model DeepSeek-V4.1-Flash-1017-UNCENSORED \
        --prompt 256 --concurrency 1 --repeat 5 --output-tokens 128 --nonce-base run1
python3 bench/bench.py decode --conc 1,2,4,8 --kind code      # and --kind prose; prefill --lens ...; needle --len 1040000 --streams 4
tests/validate.sh
```
