from datetime import datetime, timezone

from pydantic import BaseModel, Field


class DiaryEntry(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    ts: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    tags: list[str] = Field(default_factory=list, max_length=32)


class Material(BaseModel):
    text: str = Field(min_length=1)
    source: str = Field(min_length=1, max_length=512)
    course: str = Field(min_length=1, max_length=64)


class QuizItem(BaseModel):
    question: str
    answer: str
    topic: str
    source_ref: str | None = None
