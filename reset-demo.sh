#!/usr/bin/env bash
# Reset just the SQL side for the TUM demo. Leaves cognee (diary + materials)
# untouched — reingesting 42 PDFs takes 5+ min. To wipe cognee too, use
# seed.sh --fresh (separately).
#
# What this script does:
#   1. Drops the backend sqlite DB (agent_memory.db).
#   2. Runs the demo SQL seed (Odin user + courses + schedule + quiz history
#      + ~28 chat messages).
#
# Usage: ./reset-demo.sh
set -euo pipefail

cd "$(dirname "$0")/backend"

DB="agent_memory.db"
if [[ -f "$DB" ]]; then
  echo "[reset-demo] removing $DB"
  rm -f "$DB"
fi

echo "[reset-demo] running demo SQL seed"
uv run python -m scripts.seed_demo_sql

echo "[reset-demo] done"
