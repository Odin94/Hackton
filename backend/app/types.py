from datetime import datetime, timezone

from pydantic import BaseModel, Field


class DiaryEntry(BaseModel):
    text: str
    ts: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    tags: list[str] = []


class Material(BaseModel):
    text: str
    source: str
    course: str


class QuizItem(BaseModel):
    question: str
    answer: str
    topic: str
    source_ref: str | None = None
