# What makes it fast, how it works, how we run it

Each entry: **what** the mechanism is, **how** it works, **how we enable/verify it**, and whether it is
**exact** (bit-identical to the stock path), **exact in distribution** (speculative-side: every emitted token
is still verified by the target model) or **numerically equivalent** (different summation order / kernel,
quality gated by qeval + needles). Nothing here re-quantizes the published weights.

## 1. Fabric and collectives (decode is latency-bound: ~90-104 small collectives per step)

| Mechanism | How it works | Enabled / verified by | Correctness |
|---|---|---|---|
| **Kernel 6.17.0-1032 (PCIe LTR+)** | Older GB10 kernels leave LTR off on the CX7 root ports; NIC DMA reads stall and TX caps at 12.8 Gb/s per PCIe root. 1032 enables LTR: 111.9 Gb/s per function. | `ring-preflight.sh` (LTR + `--bw`) | n/a |
| **Four RDMA functions per node** | Each CX7 port is two PCIe x4 functions; both are addressed (one /24 each) so traffic spreads over 2 PCIe roots. | `IB_HCA` lists all four; NCCL `SWITCHLESS/4HCA: listener slot 0..3` | n/a |
| **Ring-only NCCL (4hca build)** | NCCL 2.30.7 patched to skip Tree/PAT setup (opposite ranks have no link) and to advertise four listener GIDs. Only big prefill collectives use it once RoCEnante is up. | `/opt/nccl-switchless-4hca-fence` (+ NCCL#2393 fence), `NCCL_SWITCHLESS_RING_ONLY=1`, 4 channels | NCCL Ring = standard all-reduce |
| **RoCEnante one-shot all-reduce/all-gather** | b12x's RDMA kernel: every rank writes its payload straight into every peer's pinned buffer and a proxy thread posts the RDMA; one round trip instead of NCCL's ring steps. ~23-60 µs vs ~56 µs+ NCCL per decode collective. | `SGLANG_ROCE_ALLREDUCE=1`, `DSV41_ROCE_GATHER`; log `RoCEnante ready: world=4 hcas=… max_size=262144` | numerically equivalent (fixed order, different from NCCL) |
| **Hardware-forwarded opposite-node paths** | Opposite ranks have no cable. The sender's NIC re-tags those RDMA packets (UDP sport 65535 → EtherType 0x88b5); the neighbour's NIC matches them with a `skip_sw` tc flower rule, restores the type, rewrites the MAC and hairpins them out the other port. No CPU, no kernel forwarding: 10.1 µs vs 8.4 µs direct at 61 KB. | `dsv41-mesh.service`, `DSV41_ROCE_RING=1`, `B12X_ROCE_PEER_HCA_MAPS` from plan.py | transport only |
| **Hairpin queue 8192 → 256 KB RoCE cap** | Forwarded bursts > ~100 KB overflowed the default 1024-packet hairpin queue (drops + go-back-N). 8192 makes up to 256 KB clean, so c2-c8 decode collectives and the draft's 129 KB vocab gathers also go one-shot. | `hairpin.sh 8192` in the mesh unit; `SGLANG_ROCE_MAX_SIZE=DSV41_ROCE_GATHER=262144` | transport only |
| **L2 prefetch** | While a RoCE collective is in flight, a side-stream kernel pulls the first 6 MB of the next weights into L2. | `DSV41_L2_PREFETCH=1`; log `RoCE collectives prefetch the next weights into L2` | exact (data untouched) |
| **ring0 node IPs on the fabric** | Gloo (SGLang's per-step CPU group), NCCL bootstrap and ssh run over 200G instead of Wi-Fi. | `GLOO/NCCL_SOCKET_IFNAME=ring0` | n/a |

## 2. GEMM / GEMV and MoE kernels

| Mechanism | How it works | Enabled by | Correctness |
|---|---|---|---|
| **EP=1, expert tensor-parallel** | Every rank holds a 576-wide slice of all 384 experts (instead of whole experts), so no expert group straggles at the MoE all-reduce (5.9 → ~2.6 ms/step). | `EP_SIZE=1` | exact sharding |
| **b12x_next routed MoE (W4A8)** | b12x main's fused MXFP4-expert kernel, in-place repack, CUDA-graph planned for bs 1-8 × verify rows 5/6; deterministic per-slot reduction instead of atomics. | `DSV41_MOE_B12X_NEXT=1`, `_DETERMINISTIC=1`; log `[moe_b12x_next] armed` | numerically equivalent to FlashInfer (rel err ~4.5% both vs fp32), deterministic |
| **MXFP8 dense on b12x** | The checkpoint's 32x32 ue8m0 block scales send FP8 GEMMs to Triton by default (52 ms/step); the b12x warp-MMA kernel (16/32-row tiles) fits decode M=6: ~17 ms. | `DSV41_MXFP8_BACKEND=b12x`, `--fp8-gemm-backend flashinfer_cutlass` | exact per-kernel |
| **Shared-expert K pad 576 → 640** | Keeps the shared expert on the b12x MXFP8 kernel (needs K % 128). | `DSV41_SHARED_PAD_K=1` | exact (zero padding) |
| **wo_a fp8 twin** | `wo_a` had only a bf16 einsum on SM121 (~11 ms/step); an exact fp8 copy runs on the MXFP8 kernel; the bf16 copy is released (−640 MB/rank). | `DSV41_WO_A_W8=1 _MID=1 _DROP=1` | exact (twin is the checkpoint's own fp8) |
| **Replicated-linear split** | Layers every rank computed in full (`wqkv_a`, `engram.wkv`) are column-split and all-gathered. | `DSV41_REPLICATED_SPLIT=…` | bit-identical (checked at boot) |
| **Fused hyper-connection stats** | Prefill hc-mix statistics in one pass over K instead of 80 slices (1.46 → 0.83 ms/call). | `DSV41_HC_FUSED=1` | bit-identical (checked on first call) |
| **Fused MoE finalize OFF** | FlashInfer's atomic bf16 finalize made greedy output vary run to run. | `SGLANG_FLASHINFER_MOE_FUSED_FINALIZE=0` | deterministic |

## 3. DSpark speculative decoding

| Mechanism | How it works | Enabled by | Correctness |
|---|---|---|---|
| **DSpark k=5** | The checkpoint ships a 3-layer draft (128 experts, top-3) that proposes 5 tokens per step; the target verifies a 6-token window in one forward. Code ~3.7-6 tok/step, prose ~2-3. | `SPEC_ALGO=DSPARK`, `DSPARK_BLOCK_SIZE=5` | exact in distribution |
| **Verify cap (confidence 0.1)** | Only drafts the confidence head expects to survive are verified; the rest reuse the anchor row's experts inside the router (`ROUTER_LIVE`), so they read no extra weights. | `DSV41_VERIFY_CAP=conf:0.1`, `DSV41_ROUTER_LIVE=1` | greedy outputs identical |
| **Draft temperature 0.7 + block verification** | Sharper draft proposals; block verification keeps sampled rows exact under the cap. | `DSV41_DRAFT_TAU=0.7`, `DSV41_BLOCK_VERIFY=1` | exact in distribution |
| **Draft head fp8 / main-proj split** | fp8 copy of the draft LM head (−1 ms/step); draft main projection split across ranks. | `DSV41_DRAFT_HEAD_FP8=1 _IMPL=tp4`, `DSV41_DRAFT_MAIN_PROJ_SPLIT=1` | exact |
| **Folded sampling + fence** | Sampled requests stay in the CUDA graph; the fence closes the sglang#40919 D2H race. | `SGLANG_DSPARK_FOLDED_SAMPLING=2`, `DSV41_FOLDED_FENCE=1` | correctness fix |
| **Full slots at c8** | DSpark's admission delayer would hold 2 slots free at 8 requests; `--min-free-slots-delay 1` disables it so 8 means 8. | EXTRA_SGLANG_ARGS | n/a |

## 4. Prefill (cold) and long context

| Mechanism | How it works | Enabled by | Correctness |
|---|---|---|---|
| **Prefill sequence parallel** | For chunks ≥ 2048 rows the per-layer all-reduce becomes reduce-scatter + all-gather and the per-row work runs on each rank's quarter of the rows (+~20% prefill). FP8 gathers send attention inputs as MXFP8 bytes. | `DSV41_PREFILL_SP=1`, `_FP8=1` | numerically equivalent (`_EXACT=1` exists, −10%) |
| **Prefill TP split of the dense indexer** | From 32k context the indexer's query rows are partitioned across ranks. | `SPARK_PREFILL_TP_SPLIT=1` | exact partition |
| **Chunked indexer (sglang#39187)** | Indexer logits scored in ≤ 1 GiB row chunks (`DSV41_INDEXER_LOGITS_BUDGET_BYTES=1073741824`; 2 GiB left the head ~1 GiB lower at no speed gain), so 8192-token chunks are safe at 1M context (a few GB of transient instead of tens). | `DSV41_INDEXER_CHUNKED=1`, `CHUNKED_PREFILL_SIZE=8192` | exact |
| **Prefill memory trim (our patch)** | Each chunk's indexer scratch is freed after the layer and stays in PyTorch's cache, which on GB10 is host RAM. The stock caching allocator reuses it chunk to chunk (no per-chunk cudaMalloc/cudaFree of multi-GB unified memory; the upstream hook emptied the cache after every chunk, ~128 multi-GB driver cycles per 1M prompt). After each forward the hook compares cached-but-free memory with the working set (the last long forward's transient peak): above working set + 1 GiB it holds the largest free blocks covering the working set, `empty_cache()` returns the rest, and the held blocks go back to the cache, at most every 10 s. Mixed batches (vision + chat + long prompts) can no longer pile up 8-13 GiB of free cache on a node. Measured: reserved flat at ~92.5 GiB through a 30-min mixed soak, every node >= 2 GiB MemAvailable, cold 1M unchanged (188-198 s). A plain `empty_cache()` every 10 s cost 17 % (scratch re-malloc); a private MemPool fragmented under mixed load. | `adapter/prefill_empty_cache.py`; `DSV41_PREFILL_TRIM_GIB=1`, `DSV41_PREFILL_TRIM_EVERY_S=10` (defaults); log `DSV41 prefill trim hook installed` | exact (allocation only) |
| **Native allocator (no expandable segments, no max_split)** | `expandable_segments:True` gives NaN logits for prefills > 64 tokens on this stack; `max_split_size_mb` stops the growing indexer scratch from reusing cached blocks (reserved ran away to 110 GiB). | `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:False` | correctness requirement |

## 5. Engram n-gram tables on SSD

| Mechanism | How it works | Enabled by |
|---|---|---|
| **NVMe row store** | The two Engram tables (203 GB) never enter memory. `pack_engram.py` repacks each rank's rows as 264-byte records (weight + scale adjacent) into `$ENGRAM_DIR/engram-l{1,14}-r{rank}of4.bin` (2 × 23.6 GiB per node), so a miss is one 4 KiB O_DIRECT read; 96 I/O threads (NVMe ~112k IOPS at QD64, 10-11 GB/s sequential here). | `OFFLOAD_MODE=nvme`, `DSV41_PACKED_DIR=/engram` |
| **Row cache** | 2 GiB 16-way set-associative cache per layer per rank (67-76% hit rate on bigram/trigram heads). | `DSV41_CACHE_GIB=2`, `DSV41_CACHE_WAYS=16` |
| **Prefetch on a side stream** | Row lookups start right after the hasher on a side stream (−2.3 to −2.9 ms/step). | `DSV41_ENGRAM_PREFETCH=1` |

## 6. KV cache, prefix reuse and eviction

- **KV is tiny**: only 4 `kv_source` layers keep full-attention KV (MLA latent, FP8): 1,670.75 bytes/token/rank, so a
  1M-token context costs 1.67 GB per rank. Sliding-window (128) layers keep a small paged SWA pool; ratio-4 layers keep a
  compressed c4 pool sized from the full pool.
- **Radix prefix cache** with **LRU eviction** (`radix_eviction_policy=lru`): finished requests leave their prefix in the
  tree; when the pool fills, the least-recently-used unreferenced prefixes are evicted first (oldest sessions rotate out).
  Only complete 256-token pages are cacheable (`page_size=256`). `--enable-cache-report` returns
  `usage.prompt_tokens_details.cached_tokens` so clients can see hits.
- **Long-session prefix retention (our fix, `DSV41_SWA_EVICT_INTERNAL_FIRST=1`).** A cached prefix is reusable only
  while the nodes covering its last 128-token window still hold SWA (sliding-window) KV. Measured with
  `--enable-metrics` (`swa_evictable_tokens`): each 8192-token prefill chunk leaves a radix node holding ~512 SWA
  tokens, so one finished 1M session holds ~62.5k SWA tokens across ~122 internal chunk nodes plus its leaf.
  SGLang's SWA eviction walk (unified cache, `SWAComponent._evict_device_next_node`; same logic in
  `SWARadixCache.evict`) follows one LRU list over internal nodes and leaves. Under a new long prefill it reaches
  an older session's **leaf** before newer sessions' internal nodes, and evicting a leaf deletes the node and
  cascades: that session's entire full KV is dropped while the full pool is mostly free. Before the fix, with
  three ~1M sessions resident every revisit was a ~210 s re-prefill; raising `--swa-prefix-tails` only delayed it.
  - `adapter/swa_evict_internal_first.py` (patch in `patches/`): a first pass tombstones **internal** nodes (oldest
    first, unlocked only, the walk's own tombstone + cascade calls, one mutation per step); internal nodes with a leaf
    closer than window+page below them are kept (short multi-turn turns need that SWA); leaves are only evicted by
    the original walk if no internal node is left. Only the order of existing actions changes.
  - Result: A,B,C at 1M then revisit: 999,680 / 999,424 / 997,120 cached tokens in **2.3-2.7 s** (was 208-213 s).
    With the fix a session costs ~its leaf in SWA, so a modest `--swa-prefix-tails` suffices (128 in config/env.tp4)
    and the full KV pool holds four resident 1M sessions.
  - Not used: `--swa-full-tokens-ratio 0.065` (ratio mode, KV pool 3.85M -> 2.29M), large `--swa-prefix-tails`
    (256 tails = 2.77 GB SWA; still evicts whole sessions once internals of newer sessions are younger).
- **Short-prompt prefix reuse (our fix, `DSV41_SWA_PROMPT_TAIL_KEEP=1024`).** A prompt shorter than one prefill chunk is
  only inserted into the radix tree when the request finishes; during decode SGLang frees its out-of-window SWA, including
  the prompt's end, so the finished insert tombstoned it and a revisit / multi-turn turn 2 could not end its match there
  (measured: 6k prompt 0 cached on the 2nd visit, 6k chat 0 % at turn 2). The adapter returns a floor from SGLang's own
  `UnifiedRadixCache.swa_retain_floor` hook (the mamba path uses the same pattern) that keeps the last 1024 prompt tokens'
  SWA through decode. Now: 2nd visits hit at every size (600 to 60k), 6k chat turn 2 = 93.7 % reuse. Slots are only freed
  later, never earlier; cost <= 1024 SWA tokens per running request. qeval unchanged (72/75, identical pass set).
- **Pool size**: pinned to `MAX_TOTAL_TOKENS=4259840` (4 x 1M sessions + headroom, ~2.05 KB/token/rank) with
  `MEM_FRACTION_STATIC=HEAD_MEM_FRACTION_STATIC=0.86` so every boot reaches the pin (boot log `max_total_num_tokens=`).
  Unpinned it floated 3.9M-4.59M with free memory at boot, and below ~4M a 4th 1M session no longer fit.
  `MAX_RUNNING_REQUESTS=8` captures CUDA graphs for bs 1-8 only.

## 7. Scheduling under mixed load (1-8 users)

`--prefill-decode-interval 8 --prefill-decode-interval-min-bs 1` + `DSV41_PREFILL_BYPASS=4096`.
`--prefill-decode-interval N` = N decode rounds after every prefill batch (an 8192-token chunk ≈ 1.6 s vs a decode round
≈ 40-60 ms, so the old N=2 gave decoders < 10 % of the time while a long prompt prefilled). `-min-bs 1` applies it only
while something is decoding, so a lone prefill runs back to back. The bypass admits small prompts (<= 4096 fresh tokens
per chunk pass) next to an in-flight long prefill instead of queueing them behind it.
Measured with `tests/latency_ab.py` (4 chat clients, ~2k-token prompts, 150 new tokens, next to back-to-back cold 200k prompts):

| config | chat p50 | chat p95 | chats in 6 min | long prefill while chats run |
|---|---|---|---|---|
| interval 2, bypass 256 (knapcio default) | 36.0 s | 59.6 s | 37 | 4,986 tok/s |
| interval 1, bypass 256 | 58.0 s | 73.9 s | 24 | 5,074 tok/s |
| interval 2, bypass 4096 | 33.3 s | 41.9 s | 45 | 4,326 tok/s |
| interval 4, bypass 4096 | 20.3 s | 27.2 s | 71 | 3,966 tok/s |
| **interval 8, min-bs 1, bypass 4096 (used)** | **14.4 s** | **20.9 s** | **98** | 3,071 tok/s |

No effect without contention: solo cold prefill 6k 4,702 / 30k 5,661 tok/s; decode code c1 84.6 / c8 235.3, prose c1 59.6 / c8 157.6.
To favour bulk prefill over chat latency, lower the interval (4 is the middle of the curve).

## 8. Loading

Fast loader at EP1: each rank reads only its TP slice of every expert into 256 MB pinned slabs (6 GB in flight): 155 GB read,
~70 s per rank; boot to Ready ~3-4 min. `scripts/drop-caches.sh` drops the page cache and compacts memory on every node
before each start: on a node with fragmented free memory the pinned slabs stall in direct compaction (~50-70 s per shard). Autotune caches kept across boots (`DSV41_AUTOTUNE_KEEP=1`).
Requires the same kernel/driver everywhere: ranks on 6.17.0-1018 / 580.159.03 died silently in this phase.
