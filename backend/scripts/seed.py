"""Seed CLI — ingests files from a directory, triggers cognify, or wipes state.

Usage:
    uv run python -m scripts.seed ingest <dir>
    uv run python -m scripts.seed index
    uv run python -m scripts.seed reset

Layout supported under <dir>:

    # Flat (legacy) — one course worth of materials + diary:
    <dir>/diary/*.md|.txt
    <dir>/materials/*.md|.txt|.pdf

    # Course-nested (for the multi-course corpus):
    <dir>/<course>/materials/*.md|.txt|.pdf
    <dir>/diary/*.md|.txt                         (diary is never nested)

Each `materials/` directory's parent-folder name becomes `Material.course`
(flat layout uses `"seed"` as the course label). PDFs are routed through
cognee's native pypdf loader; .md/.txt go through the text loader.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from app import cognee_service
from app.types import DiaryEntry

_DATE_PREFIX = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

SUPPORTED_MATERIAL_SUFFIXES = {".md", ".txt", ".pdf"}
SUPPORTED_DIARY_SUFFIXES = {".md", ".txt"}

log = logging.getLogger("seed")

MANIFEST = Path(__file__).resolve().parent.parent / "seed" / ".state.json"


@dataclass(frozen=True)
class SeedSummary:
    added: int
    skipped: int
    failed: int


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


def _load_manifest() -> dict[str, str]:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {}


def _save_manifest(m: dict[str, str]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2, sort_keys=True))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _find_material_dirs(
    root: Path, skip_courses: set[str] | None = None
) -> list[tuple[Path, str]]:
    """Discover (materials_dir, course_label) pairs under `root`.

    Supports two layouts:
    - Flat:          `<root>/materials/`            → course = "seed"
    - Course-nested: `<root>/<course>/materials/`   → course = <course>

    Hidden dirs (.git, .venv) and the top-level `diary/` are skipped.
    Courses listed in `skip_courses` are excluded.
    """
    skip = skip_courses or set()
    pairs: list[tuple[Path, str]] = []
    flat = root / "materials"
    if flat.is_dir() and "seed" not in skip:
        pairs.append((flat, "seed"))
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith(".") or sub.name in {"materials", "diary"}:
            continue
        if sub.name in skip:
            log.info("[materials] skipping course %s (--skip-courses)", sub.name)
            continue
        nested = sub / "materials"
        if nested.is_dir():
            pairs.append((nested, sub.name))
    return pairs


async def _ingest_materials(
    root: Path,
    manifest: dict[str, str],
    stats: dict[str, int],
    failed: list[tuple[str, str]],
    skip_courses: set[str] | None = None,
) -> None:
    for materials_dir, course in _find_material_dirs(root, skip_courses):
        log.info("[materials] walking %s (course=%s)", materials_dir.relative_to(root), course)
        for path in sorted(materials_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_MATERIAL_SUFFIXES:
                continue
            rel = path.relative_to(root)
            try:
                digest = _sha256_bytes(path.read_bytes())
                key = f"materials:{rel}"
                if manifest.get(key) == digest:
                    stats["skipped"] += 1
                    continue

                await cognee_service.add_material_from_file(path, course=course)
                manifest[key] = digest
                stats["added"] += 1
                log.info("[materials] added %s (course=%s)", rel, course)
            except Exception as e:
                failed.append((str(rel), f"{type(e).__name__}: {e}"))
                log.warning("[materials] FAILED %s: %s", rel, e)


async def _ingest_diary(
    root: Path,
    manifest: dict[str, str],
    stats: dict[str, int],
    failed: list[tuple[str, str]],
) -> None:
    diary_dir = root / "diary"
    if not diary_dir.is_dir():
        log.info("[diary] no %s — skipping", diary_dir)
        return
    for path in sorted(diary_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_DIARY_SUFFIXES:
            continue
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
            digest = _sha256_bytes(text.encode("utf-8"))
            key = f"diary:{rel}"
            if manifest.get(key) == digest:
                stats["skipped"] += 1
                continue

            ts = _parse_diary_date(path.stem)
            entry = DiaryEntry(text=text) if ts is None else DiaryEntry(text=text, ts=ts)
            await cognee_service.add_diary_entry(entry)
            manifest[key] = digest
            stats["added"] += 1
            log.info("[diary] added %s", rel)
        except Exception as e:
            failed.append((str(rel), f"{type(e).__name__}: {e}"))
            log.warning("[diary] FAILED %s: %s", rel, e)


async def cmd_ingest(root: Path, skip_courses: set[str] | None = None) -> SeedSummary:
    if not root.is_dir():
        log.error("%s is not a directory", root)
        sys.exit(2)

    manifest = _load_manifest()
    stats = {"added": 0, "skipped": 0}
    failed: list[tuple[str, str]] = []

    try:
        await _ingest_materials(root, manifest, stats, failed, skip_courses)
        await _ingest_diary(root, manifest, stats, failed)
    finally:
        summary = SeedSummary(
            added=stats["added"],
            skipped=stats["skipped"],
            failed=len(failed),
        )
        _save_manifest(manifest)
        log.info(
            "summary: added=%d skipped=%d failed=%d",
            summary.added, summary.skipped, summary.failed,
        )
        for p, err in failed:
            log.info("  failed: %s → %s", p, err)
        if failed:
            sys.exit(1)
    return summary


async def cmd_index() -> None:
    log.info("cognifying diary...")
    await cognee_service.cognify_dataset("diary")
    log.info("cognifying materials...")
    await cognee_service.cognify_dataset("materials")
    log.info("done")


async def cmd_sync(
    root: Path,
    *,
    skip_courses: set[str] | None = None,
    fresh: bool = False,
    skip_index: bool = False,
) -> SeedSummary:
    if fresh:
        log.info("fresh sync requested; resetting cognee state first")
        await cmd_reset()

    summary = await cmd_ingest(root, skip_courses=skip_courses)
    if skip_index:
        log.info("skip-index requested; leaving cognify step untouched")
        return summary

    if summary.added == 0:
        log.info("no new or changed files detected; skipping cognify")
        return summary

    await cmd_index()
    return summary


async def cmd_reset() -> None:
    await cognee_service.reset()
    if MANIFEST.exists():
        MANIFEST.unlink()
    log.info("reset complete (cognee pruned, manifest removed)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="seed")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser(
        "ingest",
        help="add files from <dir> (flat or course-nested; see module docstring)",
    )
    ingest.add_argument("directory", type=Path)
    ingest.add_argument(
        "--skip-courses",
        nargs="+",
        metavar="COURSE",
        default=[],
        help="course directory names to skip (e.g. Einführung_in_die_Rechnerarchitektur)",
    )

    sync = sub.add_parser(
        "sync",
        help="incrementally ingest files from <dir> and cognify only when something changed",
    )
    sync.add_argument("directory", type=Path)
    sync.add_argument(
        "--fresh",
        action="store_true",
        help="wipe cognee + manifest before ingesting",
    )
    sync.add_argument(
        "--skip-index",
        action="store_true",
        help="ingest only; do not run cognify afterwards",
    )
    sync.add_argument(
        "--skip-courses",
        nargs="+",
        metavar="COURSE",
        default=[],
        help="course directory names to skip (e.g. Einführung_in_die_Rechnerarchitektur)",
    )

    sub.add_parser("index", help="cognify both datasets")
    sub.add_parser("reset", help="wipe cognee + manifest")

    args = parser.parse_args()
    debug = os.getenv("SEED_DEBUG", "").lower() in {"1", "true", "yes", "on"}

    try:
        if args.cmd == "ingest":
            asyncio.run(cmd_ingest(args.directory, skip_courses=set(args.skip_courses)))
        elif args.cmd == "sync":
            asyncio.run(
                cmd_sync(
                    args.directory,
                    skip_courses=set(args.skip_courses),
                    fresh=args.fresh,
                    skip_index=args.skip_index,
                )
            )
        elif args.cmd == "index":
            asyncio.run(cmd_index())
        elif args.cmd == "reset":
            asyncio.run(cmd_reset())
    except SystemExit:
        raise
    except Exception as exc:
        if debug:
            raise
        log.error("seed failed: %s", exc)
        log.info("set SEED_DEBUG=1 to see the full traceback")
        sys.exit(1)


if __name__ == "__main__":
    main()
