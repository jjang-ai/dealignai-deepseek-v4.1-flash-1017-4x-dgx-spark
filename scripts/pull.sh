#!/bin/bash
# pull.sh <me 1..4> <src 1..4>... — rsync each source node's share (files_sN.txt) into $MODEL_DIR over the ring.
: "${MODEL_DIR:?}"
me=$1; shift; cd "$(dirname "$0")" || exit 1
for s in "$@"; do [ "$s" = "$me" ] && continue
  ( rsync -a --partial --files-from="files_s$s.txt" "rk$s:$MODEL_DIR/" "$MODEL_DIR/" && echo "OK from rk$s" || echo "FAIL from rk$s" ) &
done; wait; echo PULL-DONE
