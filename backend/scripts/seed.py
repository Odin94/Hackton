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
import sys
from pathlib import Path

from app import cognee_service
from app.types import DiaryEntry, Material

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
        print(f"error: {root} is not a directory", file=sys.stderr)
        sys.exit(2)

    manifest = _load_manifest()
    added = 0
    skipped = 0

    for subdir, dataset in (("diary", "diary"), ("materials", "materials")):
        ds_root = root / subdir
        if not ds_root.is_dir():
            print(f"[{dataset}] no {ds_root} — skipping")
            continue
        for path in sorted(ds_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8")
            digest = _sha256(text)
            key = f"{dataset}:{path.relative_to(root)}"
            if manifest.get(key) == digest:
                skipped += 1
                continue

            if dataset == "diary":
                await cognee_service.add_diary_entry(DiaryEntry(text=text))
            else:
                await cognee_service.add_material(
                    Material(text=text, source=path.name, course="seed")
                )
            manifest[key] = digest
            added += 1
            print(f"[{dataset}] added {path.relative_to(root)}")

    _save_manifest(manifest)
    print(f"\nsummary: added={added} skipped={skipped}")


async def cmd_index() -> None:
    print("cognifying diary...")
    await cognee_service.cognify_dataset("diary")
    print("cognifying materials...")
    await cognee_service.cognify_dataset("materials")
    print("done")


async def cmd_reset() -> None:
    await cognee_service.reset()
    if MANIFEST.exists():
        MANIFEST.unlink()
    print("reset complete (cognee pruned, manifest removed)")


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
