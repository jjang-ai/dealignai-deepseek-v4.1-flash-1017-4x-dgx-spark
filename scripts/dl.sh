#!/bin/bash
# dl.sh <filelist> <revision> — download one node's share of the checkpoint into $MODEL_DIR, resumable.
# Env: HF_REPO, MODEL_DIR (set by install.sh weights), P = parallel downloads (default 3).
# An HF token, if needed, is read from ~/.cache/huggingface/token.
: "${HF_REPO:?}" "${MODEL_DIR:?}"
REV=$2; OUT=$MODEL_DIR; mkdir -p "$OUT"
T=$(cat ~/.cache/huggingface/token 2>/dev/null)
get(){ f=$1; mkdir -p "$OUT/$(dirname "$f")"; for t in $(seq 1 30); do
  curl -sfL -C - --retry 5 --retry-all-errors ${T:+-H "Authorization: Bearer $T"} -o "$OUT/$f.part" "https://huggingface.co/$HF_REPO/resolve/$REV/$f" && mv "$OUT/$f.part" "$OUT/$f" && echo "OK $f" && return; sleep 5; done; echo "FAIL $f"; }
export -f get; export OUT HF_REPO REV T
grep -v '^$' "$1" | while read -r f; do [ -f "$OUT/$f" ] || echo "$f"; done | xargs -P "${P:-3}" -I{} bash -c 'get {}'
echo DONE
