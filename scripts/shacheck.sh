#!/bin/bash
# shacheck.sh — verify $MODEL_DIR against SHA256SUMS.hf (next to this script); writes sha.result there.
: "${MODEL_DIR:?}"
D=$(cd "$(dirname "$0")" && pwd); cd "$MODEL_DIR" || exit 1
awk '{print $2}' "$D/SHA256SUMS.hf" | xargs -P 8 -I{} sha256sum {} > "$D/sha.local" 2>&1
if diff <(sort -k2 "$D/sha.local") <(sort -k2 "$D/SHA256SUMS.hf") > "$D/sha.diff"; then echo SHA-OK; else echo SHA-MISMATCH; fi > "$D/sha.result"
