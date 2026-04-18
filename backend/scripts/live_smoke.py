"""Live end-to-end smoke for the cognee layer.

Ingests one material file, cognifies the materials dataset, generates a
small quiz, and sanity-checks dataset isolation on the (empty) diary
dataset. Intended as a deliberate manual probe, NOT part of the pytest
suite — it makes real OpenRouter + OpenAI API calls (cost ~$0.01, wall
clock ~1-3 min) and the LLM output is stochastic.

Usage:
    uv run python -m scripts.live_smoke                            # default PDF
    uv run python -m scripts.live_smoke --pdf PATH --course NAME
    uv run python -m scripts.live_smoke --topic "backprop" --n 5
    uv run python -m scripts.live_smoke --skip-ingest              # re-run quiz on existing state

Land-mines it sidesteps (discovered in iteration 16):
- Imports `app.cognee_service` before touching cognee directly so our env
  mutation (DATA_ROOT_DIRECTORY, etc.) runs before cognee's base_config
  caches its paths.
- Pops ALL_PROXY / all_proxy (Claude Code sets SOCKS; socksio not installed).
- Skips `cognee.run_startup_migrations()` — broken on fresh DBs in cognee
  1.0 (migration `ab7e313804ae` reads `acls` column metadata before base
  tables exist). Cognee's pipeline setup() creates schema lazily.

If a previous run crashed, wipe partial state with:
    find backend/.cognee_system -type f -delete
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import traceback
from pathlib import Path

# pop SOCKS before any httpx import (cognee + openai sdk would see it otherwise)
for _k in ("ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

# Ensure app/ is on path when run as a script rather than via `python -m`.
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# IMPORTANT: import app.cognee_service BEFORE touching cognee — app/__init__.py
# runs app.config.py which mutates DATA_ROOT_DIRECTORY / SYSTEM_ROOT_DIRECTORY
# before cognee caches its base_config.
from app import cognee_service  # noqa: E402
from app.cognee_service import NoDataError  # noqa: E402

DEFAULT_PDF = Path("/home/agent/workspace/Hackton/data/Einführung_in_die_Informatik/materials/folien-11a.pdf")
DEFAULT_COURSE = "Einführung_in_die_Informatik"


async def _step(label: str, coro) -> object | None:
    print(f"\n=== {label} ===", flush=True)
    try:
        result = await coro
        print(f"[{label}] OK", flush=True)
        return result
    except Exception:
        print(f"[{label}] FAILED:", flush=True)
        traceback.print_exc()
        sys.exit(1)


async def run(pdf: Path, course: str, topic: str, n: int, skip_ingest: bool) -> None:
    size_mb = pdf.stat().st_size / 1_048_576 if pdf.is_file() else 0
    print(f"PDF: {pdf}  exists={pdf.is_file()}  size={pdf.stat().st_size if pdf.is_file() else 'n/a'}", flush=True)
    if not pdf.is_file():
        print(f"FATAL: PDF not found at {pdf}", flush=True)
        sys.exit(2)

    if not skip_ingest:
        try:
            from pypdf import PdfReader
            page_count = len(PdfReader(pdf).pages)
            est_chunks = max(1, page_count * 2)
            est_min = round(est_chunks / 20 * 0.5, 1)
            est_max = round(est_chunks / 20 * 2, 1)
            print(
                f"[progress] {page_count} pages · ~{est_chunks} chunks estimated"
                f" · cognify ETA {est_min}–{est_max} min",
                flush=True,
            )
        except Exception:
            print(f"[progress] {size_mb:.1f} MB · page count unavailable", flush=True)

        t0 = time.monotonic()
        await _step(f"ingest {pdf.name} (bootstraps DB on fresh state)",
                    cognee_service.add_material_from_file(pdf, course))
        cognify_start = time.monotonic()
        await _step("cognify materials (slow)", cognee_service.cognify_dataset("materials"))
        elapsed = time.monotonic() - cognify_start
        print(f"[progress] cognify done in {elapsed:.0f}s  (total since ingest: {time.monotonic()-t0:.0f}s)", flush=True)

    print(f"\n=== generate quiz: topic={topic!r} n={n} ===", flush=True)
    try:
        items = await cognee_service.generate_quiz(topic, n=n)
    except Exception:
        print("[quiz] FAILED:", flush=True)
        traceback.print_exc()
        sys.exit(1)

    print(f"[quiz] got {len(items)} items", flush=True)
    for i, item in enumerate(items, 1):
        print(f"\n--- item {i} ---", flush=True)
        print(f"Q: {item.question}", flush=True)
        print(f"A: {item.answer}", flush=True)
        print(f"source_ref: {item.source_ref!r}", flush=True)
        print(f"topic: {item.topic!r}", flush=True)

    if items:
        sr = items[0].source_ref or ""
        if sr.startswith("text_") and sr.endswith(".txt"):
            print(f"\n[source_ref] WARNING cognee fallback hash: {sr!r}", flush=True)
        elif sr == course or sr == pdf.name:
            print(f"\n[source_ref] OK — resolves to {sr!r}", flush=True)
        else:
            print(f"\n[source_ref] unexpected shape: {sr!r}", flush=True)

    print("\n=== isolation check: query_diary (empty dataset expected) ===", flush=True)
    try:
        answer = await cognee_service.query_diary("what study patterns emerged?")
        print(f"[diary] got content-ish answer: {answer[:200]!r}", flush=True)
        if any(m in answer.lower() for m in ("no data", "no diary", "no entries", "haven't", "do not have", "don't have")):
            print("[diary] OK — reported no diary data", flush=True)
        else:
            print("[diary] SUSPICIOUS — content-ish answer for an empty dataset", flush=True)
    except NoDataError as e:
        print(f"[diary] OK — NoDataError raised: {e}", flush=True)
    except Exception:
        print("[diary] UNEXPECTED failure:", flush=True)
        traceback.print_exc()

    print("\n=== live_smoke COMPLETE ===", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="live_smoke",
        description="Manual live probe of the cognee layer. Costs real API credits.",
    )
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help=f"PDF to ingest (default: {DEFAULT_PDF.name})")
    parser.add_argument("--course", default=DEFAULT_COURSE, help="course label for node_set")
    parser.add_argument("--topic", default="polymorphism", help="quiz topic")
    parser.add_argument("--n", type=int, default=3, help="number of quiz items")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="skip ingest+cognify, go straight to quiz (use when materials dataset already exists)")
    args = parser.parse_args()

    asyncio.run(run(args.pdf, args.course, args.topic, args.n, args.skip_ingest))


if __name__ == "__main__":
    main()
