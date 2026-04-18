"""Type-level (pydantic) validation tests for backend/app/types.py."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.types import DiaryEntry, Material


def test_diary_entry_empty_text_rejected_at_model_level():
    with pytest.raises(ValidationError):
        DiaryEntry(text="")


def test_diary_entry_oversized_text_rejected():
    with pytest.raises(ValidationError):
        DiaryEntry(text="x" * 20_001)


def test_diary_entry_too_many_tags_rejected():
    with pytest.raises(ValidationError):
        DiaryEntry(text="ok", tags=["t"] * 33)


def test_diary_entry_default_tags_empty():
    assert DiaryEntry(text="ok").tags == []


def test_material_empty_fields_rejected():
    cases = [
        {"text": "", "source": "s", "course": "c"},
        {"text": "t", "source": "", "course": "c"},
        {"text": "t", "source": "s", "course": ""},
    ]
    for kwargs in cases:
        with pytest.raises(ValidationError):
            Material(**kwargs)  # type: ignore[arg-type]


def test_material_source_too_long():
    with pytest.raises(ValidationError):
        Material(text="t", source="x" * 513, course="c")


def test_material_course_too_long():
    with pytest.raises(ValidationError):
        Material(text="t", source="s", course="x" * 65)


def test_material_happy():
    m = Material(text="notes", source="ml-l3.md", course="ML-L3")
    assert m.text == "notes"
    assert m.course == "ML-L3"
