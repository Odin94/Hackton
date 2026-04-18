"""Unit tests for scripts/seed.py helpers + cmd_ingest flow."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app import cognee_service
from scripts import seed

# ----- _parse_diary_date ---------------------------------------------------

def test_parse_diary_date_happy():
    ts = seed._parse_diary_date("2026-04-08-mon")
    assert ts == datetime(2026, 4, 8, 0, 0, tzinfo=UTC)


def test_parse_diary_date_no_suffix_ok():
    ts = seed._parse_diary_date("2026-04-08")
    assert ts == datetime(2026, 4, 8, 0, 0, tzinfo=UTC)


def test_parse_diary_date_invalid_returns_none():
    assert seed._parse_diary_date("not-a-date.md") is None
    assert seed._parse_diary_date("20260408") is None  # no dashes
    assert seed._parse_diary_date("2026-13-01") is None  # bad month


# ----- _find_material_dirs -------------------------------------------------

def test_find_material_dirs_flat_layout(tmp_path: Path):
    (tmp_path / "materials").mkdir()
    (tmp_path / "diary").mkdir()  # ignored
    pairs = seed._find_material_dirs(tmp_path)
    assert pairs == [(tmp_path / "materials", "seed")]


def test_find_material_dirs_nested_layout(tmp_path: Path):
    (tmp_path / "ML-L3" / "materials").mkdir(parents=True)
    (tmp_path / "CS-101" / "materials").mkdir(parents=True)
    (tmp_path / "no-materials-here").mkdir()  # ignored (no materials/ child)
    pairs = seed._find_material_dirs(tmp_path)
    pairs_sorted = sorted((d.relative_to(tmp_path), c) for d, c in pairs)
    assert pairs_sorted == [
        (Path("CS-101/materials"), "CS-101"),
        (Path("ML-L3/materials"), "ML-L3"),
    ]


def test_find_material_dirs_mixed_layout(tmp_path: Path):
    (tmp_path / "materials").mkdir()
    (tmp_path / "ML-L3" / "materials").mkdir(parents=True)
    pairs = seed._find_material_dirs(tmp_path)
    courses = sorted(c for _, c in pairs)
    assert courses == ["ML-L3", "seed"]


def test_find_material_dirs_skips_hidden(tmp_path: Path):
    (tmp_path / ".git" / "materials").mkdir(parents=True)
    (tmp_path / "Real" / "materials").mkdir(parents=True)
    pairs = seed._find_material_dirs(tmp_path)
    courses = [c for _, c in pairs]
    assert courses == ["Real"]


# ----- cmd_ingest — flat layout (legacy) ----------------------------------

@pytest.fixture
def flat_seed_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "diary").mkdir()
    (tmp_path / "materials").mkdir()
    (tmp_path / "diary" / "2026-04-10-wed.md").write_text("wed entry")
    (tmp_path / "diary" / "not-a-date.md").write_text("undated")
    (tmp_path / "materials" / "transformers.md").write_text("transformers notes")
    (tmp_path / "materials" / "study-skills.md").write_text("misc")

    monkeypatch.setattr(seed, "MANIFEST", tmp_path / ".state.json")
    return tmp_path


@pytest.mark.asyncio
async def test_cmd_ingest_flat_layout(flat_seed_layout: Path, monkeypatch):
    add_diary = AsyncMock()
    add_material_from_file = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material_from_file", add_material_from_file)

    await seed.cmd_ingest(flat_seed_layout)

    assert add_diary.await_count == 2
    assert add_material_from_file.await_count == 2
    # Flat layout → course "seed" for every material.
    for call in add_material_from_file.await_args_list:
        assert call.kwargs["course"] == "seed"
    # Dated diary entry picks up its filename date.
    dated_call = next(
        c for c in add_diary.await_args_list
        if "wed" in c.args[0].text
    )
    assert dated_call.args[0].ts == datetime(2026, 4, 10, 0, 0, tzinfo=UTC)


# ----- cmd_ingest — course-nested layout (the new corpus) ------------------

@pytest.fixture
def nested_seed_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Mirrors amin's data/ layout: <root>/<course>/materials/*.pdf
    (tmp_path / "Analysis_für_Informatik" / "materials").mkdir(parents=True)
    (tmp_path / "Diskrete_Strukturen" / "materials").mkdir(parents=True)
    (tmp_path / "Analysis_für_Informatik" / "materials" / "Script_01.pdf").write_bytes(b"%PDF stub 1")
    (tmp_path / "Analysis_für_Informatik" / "materials" / "Script_02.pdf").write_bytes(b"%PDF stub 2")
    (tmp_path / "Diskrete_Strukturen" / "materials" / "DS_2023.pdf").write_bytes(b"%PDF stub 3")
    (tmp_path / "diary").mkdir()  # diary is always flat
    (tmp_path / "diary" / "2026-04-10-wed.md").write_text("entry")

    monkeypatch.setattr(seed, "MANIFEST", tmp_path / ".state.json")
    return tmp_path


@pytest.mark.asyncio
async def test_cmd_ingest_nested_layout_with_pdfs(nested_seed_layout: Path, monkeypatch):
    add_diary = AsyncMock()
    add_material_from_file = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material_from_file", add_material_from_file)

    await seed.cmd_ingest(nested_seed_layout)

    assert add_material_from_file.await_count == 3
    assert add_diary.await_count == 1

    # Course label comes from the parent directory name.
    courses_seen = {c.kwargs["course"] for c in add_material_from_file.await_args_list}
    assert courses_seen == {"Analysis_für_Informatik", "Diskrete_Strukturen"}

    # PDFs were passed as Path objects (file-path ingest, not tempfile write).
    for call in add_material_from_file.await_args_list:
        path = call.args[0]
        assert isinstance(path, Path)
        assert path.suffix == ".pdf"


# ----- cmd_ingest — resilience --------------------------------------------

@pytest.mark.asyncio
async def test_cmd_ingest_rerun_skips_unchanged(flat_seed_layout: Path, monkeypatch):
    add_diary = AsyncMock()
    add_material_from_file = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material_from_file", add_material_from_file)

    await seed.cmd_ingest(flat_seed_layout)
    first = add_diary.await_count + add_material_from_file.await_count

    await seed.cmd_ingest(flat_seed_layout)
    assert add_diary.await_count + add_material_from_file.await_count == first


@pytest.mark.asyncio
async def test_cmd_sync_skips_index_when_nothing_changed(flat_seed_layout: Path, monkeypatch):
    add_diary = AsyncMock()
    add_material_from_file = AsyncMock()
    cognify_dataset = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material_from_file", add_material_from_file)
    monkeypatch.setattr(cognee_service, "cognify_dataset", cognify_dataset)

    await seed.cmd_sync(flat_seed_layout)
    assert cognify_dataset.await_count == 2

    await seed.cmd_sync(flat_seed_layout)
    assert cognify_dataset.await_count == 2


@pytest.mark.asyncio
async def test_cmd_sync_fresh_resets_before_ingest(flat_seed_layout: Path, monkeypatch):
    reset = AsyncMock()
    add_diary = AsyncMock()
    add_material_from_file = AsyncMock()
    cognify_dataset = AsyncMock()
    monkeypatch.setattr(seed, "cmd_reset", reset)
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material_from_file", add_material_from_file)
    monkeypatch.setattr(cognee_service, "cognify_dataset", cognify_dataset)

    await seed.cmd_sync(flat_seed_layout, fresh=True)

    reset.assert_awaited_once()
    assert cognify_dataset.await_count == 2


@pytest.mark.asyncio
async def test_cmd_ingest_continues_past_single_file_error(flat_seed_layout: Path, monkeypatch):
    """A failure on one file must not abort the whole run."""
    call_count = 0

    async def flaky_diary(entry):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("kuzu locked on first call")

    add_material_from_file = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", flaky_diary)
    monkeypatch.setattr(cognee_service, "add_material_from_file", add_material_from_file)

    with pytest.raises(SystemExit) as excinfo:
        await seed.cmd_ingest(flat_seed_layout)
    assert excinfo.value.code == 1
    assert call_count == 2  # both diary files attempted
    assert add_material_from_file.await_count == 2
    manifest = json.loads((flat_seed_layout / ".state.json").read_text())
    # 1 successful diary + 2 materials recorded; failed diary is not.
    assert len(manifest) == 3


@pytest.mark.asyncio
async def test_cmd_ingest_missing_diary_warns_but_continues(
    tmp_path: Path, monkeypatch
):
    """No diary/ → ingest materials only, no crash."""
    (tmp_path / "materials").mkdir()
    (tmp_path / "materials" / "m.md").write_text("notes")
    monkeypatch.setattr(seed, "MANIFEST", tmp_path / ".state.json")

    add_diary = AsyncMock()
    add_material_from_file = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material_from_file", add_material_from_file)

    await seed.cmd_ingest(tmp_path)
    assert add_diary.await_count == 0
    assert add_material_from_file.await_count == 1


@pytest.mark.asyncio
async def test_cmd_ingest_no_materials_dir_processes_diary(
    tmp_path: Path, monkeypatch
):
    """No materials/ anywhere → diary still ingests."""
    (tmp_path / "diary").mkdir()
    (tmp_path / "diary" / "2026-04-10.md").write_text("entry")
    monkeypatch.setattr(seed, "MANIFEST", tmp_path / ".state.json")

    add_diary = AsyncMock()
    add_material_from_file = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material_from_file", add_material_from_file)

    await seed.cmd_ingest(tmp_path)
    assert add_diary.await_count == 1
    assert add_material_from_file.await_count == 0
