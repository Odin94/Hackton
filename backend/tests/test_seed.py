"""Unit tests for scripts/seed.py helpers + cmd_ingest flow."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app import cognee_service
from scripts import seed


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


def test_parse_course_happy():
    assert seed._parse_course("ml-l3-transformers") == "ML-L3"
    assert seed._parse_course("cs-101-intro") == "CS-101"


def test_parse_course_no_hyphen_returns_none():
    assert seed._parse_course("notes") is None


def test_parse_course_leading_digits_returns_none():
    # Must start with a letter token.
    assert seed._parse_course("123-abc") is None


# ----- cmd_ingest integration-style tests --------------------------------

@pytest.fixture
def seed_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a realistic ingest directory and point the manifest at a tmp location."""
    (tmp_path / "diary").mkdir()
    (tmp_path / "materials").mkdir()
    (tmp_path / "diary" / "2026-04-10-wed.md").write_text("wed entry")
    (tmp_path / "diary" / "not-a-date.md").write_text("undated")
    (tmp_path / "materials" / "ml-l3-transformers.md").write_text("transformers notes")
    (tmp_path / "materials" / "no-course.md").write_text("misc")

    monkeypatch.setattr(seed, "MANIFEST", tmp_path / ".state.json")
    return tmp_path


@pytest.mark.asyncio
async def test_cmd_ingest_happy_path(seed_layout: Path, monkeypatch):
    add_diary = AsyncMock()
    add_material = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material", add_material)

    await seed.cmd_ingest(seed_layout)

    assert add_diary.await_count == 2
    assert add_material.await_count == 2
    # Verify filename date was parsed for the dated diary file.
    dated_call = next(
        c for c in add_diary.await_args_list
        if "wed" in c.args[0].text
    )
    assert dated_call.args[0].ts == datetime(2026, 4, 10, 0, 0, tzinfo=UTC)
    # Verify course prefix extraction on materials.
    course_call = next(
        c for c in add_material.await_args_list
        if "transformers" in c.args[0].text
    )
    assert course_call.args[0].course == "ML-L3"
    # Manifest persisted.
    assert (seed_layout / ".state.json").exists()


@pytest.mark.asyncio
async def test_cmd_ingest_rerun_skips_unchanged(seed_layout: Path, monkeypatch):
    add_diary = AsyncMock()
    add_material = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material", add_material)

    await seed.cmd_ingest(seed_layout)
    first_count = add_diary.await_count + add_material.await_count

    await seed.cmd_ingest(seed_layout)
    # Second run adds nothing — SHA256 matches in manifest.
    assert add_diary.await_count + add_material.await_count == first_count


@pytest.mark.asyncio
async def test_cmd_ingest_continues_past_single_file_error(seed_layout: Path, monkeypatch):
    """A failure on one file must not abort the whole run."""
    call_count = 0

    async def flaky_diary(entry):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("kuzu locked on first call")

    add_material = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", flaky_diary)
    monkeypatch.setattr(cognee_service, "add_material", add_material)

    with pytest.raises(SystemExit) as excinfo:
        await seed.cmd_ingest(seed_layout)
    # Non-zero exit because one file failed.
    assert excinfo.value.code == 1
    # But the second diary file AND both materials still got through.
    assert call_count == 2  # both diary files attempted
    assert add_material.await_count == 2
    # Manifest is persisted despite the failure.
    assert (seed_layout / ".state.json").exists()
    manifest = json.loads((seed_layout / ".state.json").read_text())
    # The successful diary + both materials are recorded; the failed one is not.
    assert len(manifest) == 3


@pytest.mark.asyncio
async def test_cmd_ingest_missing_subdir_warns_but_continues(
    tmp_path: Path, monkeypatch
):
    """If <root>/diary is missing, ingest the other dataset and continue."""
    (tmp_path / "materials").mkdir()
    (tmp_path / "materials" / "m.md").write_text("notes")
    monkeypatch.setattr(seed, "MANIFEST", tmp_path / ".state.json")

    add_diary = AsyncMock()
    add_material = AsyncMock()
    monkeypatch.setattr(cognee_service, "add_diary_entry", add_diary)
    monkeypatch.setattr(cognee_service, "add_material", add_material)

    await seed.cmd_ingest(tmp_path)
    assert add_diary.await_count == 0
    assert add_material.await_count == 1
