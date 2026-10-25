#!/usr/bin/env bash
# drop-caches.sh — flush the page cache and compact memory on every node before an engine start.
# GB10: CUDA's free-memory query does not count reclaimable page cache, so a node that just read the 510 GB checkpoint
# (download, SHA check, rsync) looks ~10 GB short and SGLang's TP balance check refuses to boot
# ("The memory capacity is unbalanced ... pre_model_load_memory=..."). After hours of engine restarts a node's free memory
# is also fragmented (no free blocks of order >= 9): the fast loader's pinned slabs and GPU allocations then stall in
# direct compaction and that node's weight load drops from ~1 s to ~50-70 s per shard (measured). Compacting first
# restores the large blocks. Safe at any time with the engine stopped; the engine re-reads what it needs.
for k in 1 2 3 4; do
  ssh -o BatchMode=yes rk$k 'sync; echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null; echo 1 | sudo tee /proc/sys/vm/compact_memory >/dev/null
    echo "$(hostname): $(free -g | awk "/Mem/{print \"available\",\$7,\"GiB\"}"), order>=9 free blocks $(awk "/Normal/{print \$14+\$15+\$16}" /proc/buddyinfo)"' &
done; wait
