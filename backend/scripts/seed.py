"""Seed CLI — ingests files from a directory, triggers cognify, or wipes state.

Usage:
    uv run python -m scripts.seed ingest <dir>
    uv run python -m scripts.seed index
    uv run python -m scripts.seed reset
"""
import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from datetime import UTC, datetime, time
from pathlib import Path

from app import cognee_service
from app.types import DiaryEntry, Material

_DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_COURSE_PREFIX = re.compile(r"^([a-zA-Z]+-[a-zA-Z0-9]+)", re.ASCII)


def _parse_diary_date(stem: str) -> datetime | None:
    """Parse leading YYYY-MM-DD from a diary filename stem. Returns UTC midnight or None."""
    m = _DATE_PREFIX.match(stem)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime.combine(datetime(y, mo, d).date(), time.min, tzinfo=UTC)
    except ValueError:
        return None


def _parse_course(stem: str) -> str | None:
    """Parse leading course token like 'ml-l3' → 'ML-L3'. Returns None if no match."""
    m = _COURSE_PREFIX.match(stem)
    return m.group(1).upper() if m else None

log = logging.getLogger("seed")

SUPPORTED_SUFFIXES = {".md", ".txt"}
MANIFEST = Path(__file__).resolve().parent.parent / "seed" / ".state.json"


def _load_manifest() -> dict[str, str]:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {}


def _save_manifest(m: dict[str, str]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def cmd_ingest(root: Path) -> None:
    if not root.is_dir():
        log.error("%s is not a directory", root)
        sys.exit(2)

    manifest = _load_manifest()
    added = 0
    skipped = 0
    failed: list[tuple[str, str]] = []  # (path, error-message)

    try:
        for subdir, dataset in (("diary", "diary"), ("materials", "materials")):
            ds_root = root / subdir
            if not ds_root.is_dir():
                log.info("[%s] no %s — skipping", dataset, ds_root)
                continue
            for path in sorted(ds_root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                    digest = _sha256(text)
                    key = f"{dataset}:{path.relative_to(root)}"
                    if manifest.get(key) == digest:
                        skipped += 1
                        continue

                    if dataset == "diary":
                        ts = _parse_diary_date(path.stem)
                        entry = DiaryEntry(text=text) if ts is None else DiaryEntry(text=text, ts=ts)
                        await cognee_service.add_diary_entry(entry)
                    else:
                        course = _parse_course(path.stem) or "seed"
                        await cognee_service.add_material(
                            Material(text=text, source=path.name, course=course)
                        )
                    manifest[key] = digest
                    added += 1
                    log.info("[%s] added %s", dataset, path.relative_to(root))
                except Exception as e:
                    failed.append((str(path.relative_to(root)), f"{type(e).__name__}: {e}"))
                    log.warning("[%s] FAILED %s: %s", dataset, path.relative_to(root), e)
    finally:
        _save_manifest(manifest)
        log.info("summary: added=%d skipped=%d failed=%d", added, skipped, len(failed))
        for p, err in failed:
            log.info("  failed: %s → %s", p, err)
        if failed:
            sys.exit(1)


async def cmd_index() -> None:
    log.info("cognifying diary...")
    await cognee_service.cognify_dataset("diary")
    log.info("cognifying materials...")
    await cognee_service.cognify_dataset("materials")
    log.info("done")


async def cmd_reset() -> None:
    await cognee_service.reset()
    if MANIFEST.exists():
        MANIFEST.unlink()
    log.info("reset complete (cognee pruned, manifest removed)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="seed")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest", help="add files from <dir>/diary and <dir>/materials")
    ingest.add_argument("directory", type=Path)

    sub.add_parser("index", help="cognify both datasets")
    sub.add_parser("reset", help="wipe cognee + manifest")

    args = parser.parse_args()

    if args.cmd == "ingest":
        asyncio.run(cmd_ingest(args.directory))
    elif args.cmd == "index":
        asyncio.run(cmd_index())
    elif args.cmd == "reset":
        asyncio.run(cmd_reset())


if __name__ == "__main__":
    main()
