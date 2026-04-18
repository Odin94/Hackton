#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/backend"

fresh=0
pass_args=()

while (($#)); do
  case "$1" in
    --fresh)
      fresh=1
      pass_args+=("$1")
      ;;
    *)
      pass_args+=("$1")
      ;;
  esac
  shift
done

if [[ "${SEED_QUIET_THREADS:-1}" == "1" ]]; then
  export TOKENIZERS_PARALLELISM=false
  export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
  export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
  export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
  export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"
  export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-1}"
fi

export COGNEE_CHUNKS_PER_BATCH="${COGNEE_CHUNKS_PER_BATCH:-25}"

current_ulimit="$(ulimit -n)"
if [[ "$current_ulimit" != "unlimited" ]] && (( current_ulimit < 65536 )); then
  ulimit -n 65536 || true
fi

if (( fresh )); then
  echo "=== Fresh seed: reset + ingest + index ==="
else
  echo "=== Incremental seed: ingest + index only if data changed ==="
fi

echo "=== Using lighter defaults: nice=10, chunks_per_batch=${COGNEE_CHUNKS_PER_BATCH}, threads=${OMP_NUM_THREADS:-1} ==="
cmd=(uv run python -m scripts.seed sync ../data)
if ((${#pass_args[@]})); then
  cmd+=("${pass_args[@]}")
fi
nice -n 10 "${cmd[@]}"

echo "=== Done ==="
