#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/backend"

echo "=== Wiping cognee data ==="
uv run python -m scripts.seed reset

echo "=== Ingesting diary + materials ==="
uv run python -m scripts.seed ingest ../data

echo "=== Building knowledge graph (takes ~5 min) ==="
uv run python -m scripts.seed index

echo "=== Done ==="
