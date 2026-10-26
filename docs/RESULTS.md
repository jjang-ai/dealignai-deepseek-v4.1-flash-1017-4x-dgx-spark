# Measured results (4x DGX Spark ring, TP4, DeepSeek-V4.1-Flash-1017 UNCENSORED FP8)

All numbers from `bench/bench.py` and `tests/` run on rk3 (not the head) against the fabric endpoint, unless noted.
Decode = `usage.completion_tokens / elapsed` per request (never SSE chunk counts: a DSpark step carries several
tokens), temperature 0, thinking off, 256 new tokens, median of 2 waves after a warm-up wave. Prefill = cold, unique
real text (Python/C sources + docs), `max_tokens=1`, nothing from the prefix cache. Needles: one passphrase hidden
at the given depth in real text, exact-sized with the server tokenizer.

## Profile measured (config/env.tp4)

Engine knapcio/DeepSeek-V4.1-Flash-4x-DGX-Spark-TP4 @ 506fb10 + `patches/knapcio-506fb10-dsv411017.patch`, base
`lmsysorg/sglang:dev-dsv41@sha256:b4a4745f…` flattened to one layer, ring NCCL 4hca + #2393 fence, RoCEnante mesh
256 KB, EP=1, DSpark k=5 with verify cap conf:0.1, MAX_RUNNING_REQUESTS=8, CHUNKED_PREFILL_SIZE=8192,
MEM_FRACTION_STATIC=0.86, **MAX_TOTAL_TOKENS=4259840** (KV pool pinned), DSV41_INDEXER_LOGITS_BUDGET_BYTES=1 GiB,
DSV41_CACHE_GIB=2 (Engram rows on NVMe), `--swa-prefix-tails 128`, `--prefill-decode-interval 8`, prefill memory trim
(docs/PERFORMANCE.md §4). Kernel 6.17.0-1032-nvidia, driver 580.178.04 on all nodes. Context 1,048,576 per request.

## Validation (tests/validate.sh, one run, in order, on an image built fresh by `install.sh image` from this repository)

| test | result |
|---|---|
| 5 x 1M sessions (pool holds 4) | cold ~1M prefills 188.1-191.7 s (first after boot 210.3 s); all 4 revisits **100 % cached in 2.6-3.0 s**; the 5th session evicts the oldest, the 3 most recent stay cached (2.7-3.1 s): **PASS** |
| 30-min long-prefix soak (200k-950k prefixes + fresh 0.5-4k suffixes, 1 and 4 streams) | **896 / 896 OK**, all warm, 0 failures |
| 30-min mixed soak, 8 clients | 0 failures; chat n=342 p50 19.2 s p95 90.4 s; vision n=262 p50 7.2 s; 50-200k prompts n=50 p50 31.3 s; 600k session revisits **183/183 cache hits** (p50 7.8 s) |
| engine log | 0 Xid / NCCL WARN / CUDA error / OOM / tracebacks |
| kernel | 0 Xid on every node |
| lowest MemAvailable during the run (1 s black box) | rk1 3.58 GiB, rk2 5.45, rk3 5.06, rk4 5.32 |
| API suite on the same deployment | 32/32 |

The same proof on the previous build of the same code (run before the package rebuild): LRU PASS, 926/926, mixed 0
failures 200/200, lowest MemAvailable 2.77 GiB.

## Decode (aggregate tok/s, per-stream in brackets)

| | c1 | c2 | c4 | c8 |
|---|---|---|---|---|
| code | 83.4 | 120.5 (61.0) | 170.5 (45.5) | 230.8 (32.0) |
| prose | 57.0 | 78.9 (40.0) | 112.6 (28.7) | 157.9 (20.5) |

## Edge cases (with an API key set, on the packaged deployment)

| case | result |
|---|---|
| 5 concurrent ~900k prompts = 4.49M tokens, more than the 4.26M KV pool | 5/5 needles; the scheduler queues what does not fit; 5,069 tok/s aggregate; lowest MemAvailable 4.6 GiB |
| 24 concurrent chats (3x the 8 slots) | 24/24 answered, 3.9 s wall |
| 5 x 1M LRU with the key | PASS: revisits 2.6-3.1 s at 100 %, the oldest session evicted |
| images: 4K, 4000x300 panorama, 8x8, alpha PNG, JPEG, WebP, 32 in one request | 9/9 (tests/multimodal_edge.py); corrupt or non-image data is a clean 400, engine unaffected |
| video (`examples/video.py`, 8 frames of a 4-colour clip) | "red, green, blue, yellow" (1,893 prompt tokens) |
| API key | no key / wrong key: 401 on chat, models, loads; correct key: 200; `/health`, `/metrics` open |
| `./dsv41 reboot rk4` | stop -> reboot -> fabric-heal restarted the node's mesh -> preflight PASS -> Ready in 4 min 17 s, no manual step |
| `./dsv41 unload` / `load` | all containers gone, 110-111 GiB free per node, autostart off / on, Ready in ~3 min, API 42/42 |

## Decode on the upstream harnesses (docs/COMPARISON.md)

| harness | result here | published upstream |
|---|---|---|
| knapcio speedbench 0.3, 73 prompts c1, all-prompt geomean | **84.6 tok/s** [80.0, 89.5]; prose 64.4, code 104.3, structured 102.9, agentic 92.8, multilingual 67.2, long ctx 88.3; TTFT 216 ms | knapcio 87.5 (switched RoCE, official 1017) |
| matrix harness (`bench-miaai.py`, 128 numbered words) c1 / c4 / c8 per stream | **138.5** / 74.2 / 51.6 tok/s; TTFT 8K 1.39 s, 32K 5.0 s, 131K 19.7 s | colonel-otto ring 126.7-132.0 / 70.8 / 56.8; 1.56 / 6.04 / 24.3 s |

## Cold prefill (real text)

| prompt tokens | 22.5k | 77.4k | 121k | 342k | 514k | ~1M |
|---|---|---|---|---|---|---|
| tok/s | 5,699 | 6,028 | 6,069 | 5,908 | 5,700 | ~5,150 (190-195 s) |

## 1M-context needles

| test | result |
|---|---|
| 4 concurrent ~1.04M-token prompts, needle at depth 0.10 / 0.37 / 0.63 / 0.90 | **4/4 PASS** (1,039,533 / 1,039,721 / 1,035,510 / 1,039,927 tokens); 4,154,691 prompt tokens in 826 s wall (5,031 tok/s aggregate) |

## Correctness

| gate | result |
|---|---|
| qeval (75 auto-scored tasks, c1, greedy) | **72/75**, pass set identical to the upstream reference runs; misses `code_interval_intersect`, `json_escape`, `math_m9` (same as upstream). Measured before the KV pin and the prefill memory trim, which change allocation and cache sizing only, not numerics |
| boot smoke | arithmetic, structured output, tool round trip, native vision, batch-of-8 sanity: pass on every boot |
| compact gather, replicated split, fused hc stats | self-checked bit-identical at boot |

## Long-prefill memory under load (what the prefill memory hook is for)

On GB10 PyTorch's allocator cache is host RAM. Designs tried for the long-prefill transient, each under the 30-min mixed
soak above (all with the pinned 4.26M pool); lowest MemAvailable on any node:

| prefill transient handling | 30-min mixed soak | lowest MemAvailable |
|---|---|---|
| private MemPool for long-prefill scratch | reserved crept to 98 GiB (10 GiB cached but free) | 0.91 GiB |
| same, pool dropped and re-primed when it grew | orphaned partial pools, reserved up to 102 GiB | 0.82 GiB |
| `max_split_size_mb:256` + periodic empty_cache | scratch not reused, reserved 110 GiB within minutes, nodes stalled in reclaim | 0 (watchdog reboots) |
| periodic empty_cache above 4 GiB free, every 30 s | reserved 92-93 GiB | 1.68 GiB |
| **working-set-preserving trim (shipped)** | reserved flat ~92.5-93 GiB, 1M prefill unchanged | **2.77 GiB** |

A plain empty_cache every 10 s held memory as well but cost ~17 % of 1M prefill speed (scratch re-allocated each time).

## Fabric
All 16 directed links 111.84-111.97 Gb/s (`ib_write_bw`, 1 MiB, 4 QPs) after the kernel fix (was 12.8 Gb/s
on 6.17.0-1014 / 7.0.0-1019). Opposite-node RDMA through the neighbour's hardware forwarding: 10.1 µs at 61 KB
(direct neighbour 8.4 µs).

## API completeness (tests/api_suite.py, on the packaged deployment)

**32/32** checks pass: `/v1/models`; chat with usage, system prompt, multi-turn, stop sequences, `max_tokens` /
`finish_reason=length`, `n=3`, streaming with `stream_options.include_usage`; client disconnect mid-stream (engine
healthy afterwards); over-length prompt → clean HTTP 400; logprobs + top_logprobs; thinking on (`reasoning_content`),
`enable_thinking` alias, thinking off, `reasoning_effort` low/max (longer reasoning at max); tool call, parallel tool
calls, forced `tool_choice`, tool-result round trip; `response_format` json_schema and json_object; images via data URI,
HTTP URL, multiple images, spatial question (4 quadrants), image + thinking, and a mixed image+text batch of 8
(8/8 correct); `/v1/completions`, `/v1/responses`, `/v1/messages` (Anthropic).

Known limitations (not fixed):
- `seed` with temperature > 0 is not reproducible: SGLang plumbs it to `sampling_seed`, but the DSpark draft /
  block-verify path does not use it (only the plain sampler and EAGLE verify do). Greedy (T=0) is deterministic.
- (fixed) First-visit tail of a short prompt was not reusable (SGLang freed the prompt-end SWA during decode); fixed by
  `DSV41_SWA_PROMPT_TAIL_KEEP=1024`: every size now hits on the 2nd visit; multi-turn 6k chat 93.7 % reuse at turn 2,
  30k chat 99 %.

## DSpark acceptance per workload (bench/dspark_accept.py, c1, k=5 → max 6 tokens/verify step)
| workload | tokens/step | draft accept rate | c1 tok/s |
|---|---|---|---|
| JSON | 5.41 | 0.89 | 128.1 |
| code T=0 | 4.23 | 0.65 | 105.1 |
| code T=0.7 | 3.90 | 0.58 | 97.0 |
| thinking T=0 | 3.79 | 0.56 | 100.3 |
| prose T=0 | 1.89 | 0.18 | 58.8 |
| prose T=0.7 | 1.80 | 0.16 | 52.6 |
Sampling (T=0.7) costs ~8 % acceptance thanks to the #45518 softmax clamp in the engine image; prose is limited by the
draft itself (k=5 + conf:0.1 cap is the TP4 optimum, prose unchanged by the cap).

## Final tuning evidence (things tried, measured, and kept or rejected)
| lever | result | decision |
|---|---|---|
| GPU clock lock `nvidia-smi -lgc 3003` | clocks only reach ~2.5 GHz under load (default ~2.4); code c1 83.8 vs 84.6, c8 213.6 vs 235.3 | rejected (reverted, `-rgc`) |
| DSpark verify cap off | prose c1 46.9 vs 58.8 (-20 %), code/json ≈ | keep the cap |
| verify cap conf:0.05 | prose 55.7, code 100.8, thinking 98.1, json 127.6, code c8 231.8 | conf:0.1 better |
| verify cap conf:0.2 | prose 56.3, code 102.5, thinking 99.0, json 121.8, code c8 223.3 | conf:0.1 better |
| **verify cap conf:0.1** | prose 58.8, code 105.1, thinking 100.3, json 128.1, code c8 235.3 | **kept** |
| DSpark block size k=4 | boot fails: the checkpoint's draft heads are shaped for block 5 (CUDA-graph matmul 40x4352 vs 5376x1) | k=5 is the only valid value |
| scheduler interval 1/2/4/8 + bypass 256/4096 | see docs/PERFORMANCE.md §7 | interval 8 + min-bs 1 + bypass 4096 |
| trim finished-leaf SWA to the window (384) | measured: older 1M sessions stay resident but become unmatchable (A,B,A at 1M missed) | rejected; keep 8704 (one chunk) |
| DSpark draft tau 0.5 / 1.0 (vs 0.7) | code T=0.7: 96.0 / 91.4 vs 97.0 tok/s (3.84 / 3.79 vs 3.90 tok/step); prose T=0.7: 55.6 / 54.8 vs 52.6; T=0 identical | keep 0.7 (differences within run noise; code, the main workload, is best at 0.7) |
| Engram row cache 4 GiB | costs ~1.2M KV tokens (below 4 x 1M) | keep 2 GiB |

`seed` with T>0 under DSpark (not implemented, by decision): randomness is drawn inside captured CUDA graphs in at least
three places (draft in-graph Philox exponential noise in `dspark_draft_sampler`, draft exponential/multinomial sampling in
`dspark_draft`, verify/block-verify), upstream's own seeded EAGLE verify notes its draft side stays non-deterministic, and
even a fully seeded path would not be bit-reproducible across different batch compositions (OpenAI documents `seed` as
best-effort for the same reason). Greedy (T=0) output is deterministic.

Note on qeval's "median single-stream tok/s" (85.0 on the MiaAI line vs 81.5-82.2 on the final knapcio config): qeval is a
correctness gate over 75 mixed tasks (many short answers, where time-to-first-token dominates and the knapcio scheduler /
bypass logic adds a few ms); it is not a decode benchmark. On the controlled decode bench the final config is faster at every
concurrency (code c1 84.6 vs 79.7, c8 235.3 vs 223.3; prose c1 59.6 vs 54.7) and cold prefill is +15-20 %. Two repeated qeval runs on the same final config gave 81.2 and 86.3 median tok/s (both 72/75,
same pass set), so differences of a few tok/s between qeval runs are noise.

## Earlier baseline — MiaAI-Lab production line (for comparison)

MiaAI-Lab @ 64b02de, image `dsv41-4x-spark:canary-roce`, chunk 4096, 16 slots, fraction 0.80, cache 4 GiB.
qeval 72/75. KV pool 3.85M.

| decode | c1 | c2 | c4 | c8 | c16 |
|---|---|---|---|---|---|
| prose | 54.7 | 79.7 | 112.2 | 153.7 | 209.2 |
| code | 79.7 | 114.2 | 161.5 | 223.3 | 323.1 |

| cold prefill | 3.2k | 14.8k | 32k | 66k | 132k | 253k | ~1M (4 conc.) |
|---|---|---|---|---|---|---|---|
| tok/s | 4,384 | 3,890 | 4,871 | 4,725 | 4,612 | 4,684 | ~2.6k at depth |

At fraction 0.84 / chunk 4096 the head's MemAvailable fell to 0.03 GiB during 4 x 1M — too tight; not used.

