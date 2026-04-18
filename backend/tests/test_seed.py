"""Unit tests for scripts/seed.py helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from scripts import seed


def test_parse_diary_date_happy():
    ts = seed._parse_diary_date("2026-04-08-mon")
    assert ts == datetime(2026, 4, 8, 0, 0, tzinfo=timezone.utc)


def test_parse_diary_date_no_suffix_ok():
    ts = seed._parse_diary_date("2026-04-08")
    assert ts == datetime(2026, 4, 8, 0, 0, tzinfo=timezone.utc)


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
