"""Demo SQL seed — Odin user + 3 courses + schedule + past quiz results + chat history.

Run from backend/:
    uv run python -m scripts.seed_demo_sql

Idempotent: drops all of Odin's rows (cascading), then reinserts.
Uses real ISO datetimes dated to late Sept – Nov 2025 (the narrative diary arc).
The demo clock (CURRENT_DATE_OVERRIDE) pins "now" to 2025-11-14 so these look
like recent history from the LLM's perspective.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from agent.database import AsyncSessionLocal, create_all_tables
from agent.models import (
    ChatMessage,
    Course,
    Deadline,
    Quiz,
    QuizResult,
    ScheduleEvent,
    User,
)

log = logging.getLogger("seed_demo_sql")

DEMO_USERNAME = "odin"
DEMO_NAME = "Odin"
DEMO_EMAIL = "odin@example.com"

COURSES = [
    "Einführung in die Informatik",
    "Diskrete Strukturen",
    "Analysis für Informatik",
]


def _dt(year: int, month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


async def _ensure_user(session) -> User:
    user = await session.scalar(select(User).where(User.username == DEMO_USERNAME))
    if user is None:
        user = User(username=DEMO_USERNAME, name=DEMO_NAME, email=DEMO_EMAIL)
        session.add(user)
        await session.flush()
        log.info("created user id=%d", user.id)
    else:
        log.info("reusing user id=%d", user.id)
    return user


async def _wipe_user_data(session, user_id: int) -> None:
    # Cascade deletes on the ORM relationships handle children when we delete
    # the parent rows. Order matters: drop weak-ref children first to be safe.
    await session.execute(delete(QuizResult).where(QuizResult.user_id == user_id))
    await session.execute(delete(Quiz).where(Quiz.user_id == user_id))
    await session.execute(delete(ScheduleEvent).where(ScheduleEvent.user_id == user_id))
    await session.execute(delete(Deadline).where(Deadline.user_id == user_id))
    await session.execute(delete(Course).where(Course.user_id == user_id))
    await session.execute(delete(ChatMessage).where(ChatMessage.user_id == user_id))
    log.info("wiped prior data for user_id=%d", user_id)


async def _seed_courses(session, user_id: int) -> dict[str, Course]:
    by_name: dict[str, Course] = {}
    for name in COURSES:
        course = Course(user_id=user_id, name=name)
        session.add(course)
        by_name[name] = course
    await session.flush()
    log.info("seeded %d courses", len(by_name))
    return by_name


async def _seed_schedule(session, user_id: int, courses: dict[str, Course]) -> None:
    """A recent-history schedule spanning the term. Two upcoming events so
    'upcoming deadlines/events' in the LLM context show something real."""
    ds = courses["Diskrete Strukturen"]
    einf = courses["Einführung in die Informatik"]
    anal = courses["Analysis für Informatik"]

    events = [
        # Recent past lectures (drives the 'revise' framing for the demo).
        ScheduleEvent(
            user_id=user_id, course_id=ds.id,
            type="lecture", name="DS: Formale Sprachen & Automaten",
            start_datetime=_dt(2025, 11, 13, 10, 0),
            end_datetime=_dt(2025, 11, 13, 12, 0),
        ),
        ScheduleEvent(
            user_id=user_id, course_id=ds.id,
            type="lecture", name="DS: NFA → DFA Konvertierung",
            start_datetime=_dt(2025, 11, 14, 10, 0),
            end_datetime=_dt(2025, 11, 14, 12, 0),
        ),
        ScheduleEvent(
            user_id=user_id, course_id=einf.id,
            type="lecture", name="EInf: Laufzeitanalyse",
            start_datetime=_dt(2025, 11, 12, 14, 0),
            end_datetime=_dt(2025, 11, 12, 16, 0),
        ),
        ScheduleEvent(
            user_id=user_id, course_id=anal.id,
            type="lecture", name="Analysis: Reihen & Konvergenztests",
            start_datetime=_dt(2025, 11, 11, 10, 0),
            end_datetime=_dt(2025, 11, 11, 12, 0),
        ),
        # Upcoming — demo sees these as "next 7 days" when clock is 2025-11-14.
        ScheduleEvent(
            user_id=user_id, course_id=ds.id,
            type="tutorium", name="DS: Übung Automaten",
            start_datetime=_dt(2025, 11, 17, 14, 0),
            end_datetime=_dt(2025, 11, 17, 16, 0),
        ),
        ScheduleEvent(
            user_id=user_id, course_id=anal.id,
            type="lecture", name="Analysis: Potenzreihen",
            start_datetime=_dt(2025, 11, 18, 10, 0),
            end_datetime=_dt(2025, 11, 18, 12, 0),
        ),
        ScheduleEvent(
            user_id=user_id, course_id=einf.id,
            type="lecture", name="EInf: Sortieralgorithmen",
            start_datetime=_dt(2025, 11, 19, 14, 0),
            end_datetime=_dt(2025, 11, 19, 16, 0),
        ),
    ]
    for e in events:
        session.add(e)
    await session.flush()

    deadlines = [
        Deadline(
            user_id=user_id, course_id=ds.id, name="DS Übungsblatt 8",
            datetime=_dt(2025, 11, 18, 23, 59),
        ),
        Deadline(
            user_id=user_id, course_id=einf.id, name="EInf Abgabe H5",
            datetime=_dt(2025, 11, 20, 23, 59),
        ),
        Deadline(
            user_id=user_id, course_id=anal.id, name="Analysis Übungsblatt 5",
            datetime=_dt(2025, 11, 21, 23, 59),
        ),
    ]
    for d in deadlines:
        session.add(d)
    await session.flush()
    log.info("seeded %d schedule events + %d deadlines", len(events), len(deadlines))


async def _seed_quiz_history(session, user_id: int, courses: dict[str, Course]) -> None:
    """Ten past quizzes — the trajectory shows someone who struggled early,
    stabilized mid-term, and recently improved. The LLM sees a learner making
    progress, not a static snapshot."""
    ds = courses["Diskrete Strukturen"]
    einf = courses["Einführung in die Informatik"]
    anal = courses["Analysis für Informatik"]

    # (course, title, topic, taken_at, correct, false)
    arc: list[tuple[Course, str, str, datetime, int, int]] = [
        (einf, "EInf: Grundlagen Check", "Python Grundlagen", _dt(2025, 9, 25), 3, 2),
        (ds, "DS: Aussagenlogik", "Aussagenlogik, Tautologien", _dt(2025, 10, 2), 3, 2),
        (einf, "EInf: Listen & Rekursion", "Rekursion, Listen", _dt(2025, 10, 8), 4, 1),
        (anal, "Analysis: Folgen-Grundlagen", "Folgen, Konvergenz, epsilon-N", _dt(2025, 10, 16), 3, 2),
        (ds, "DS: Mengenlehre", "Mengen, Relationen", _dt(2025, 10, 22), 4, 1),
        (einf, "EInf: Datenstrukturen", "Arrays, Hashmaps", _dt(2025, 10, 28), 4, 1),
        (ds, "DS: Chomsky-Hierarchie", "Chomsky-Hierarchie, reguläre Sprachen", _dt(2025, 11, 3), 4, 1),
        (anal, "Analysis: Reihen-Konvergenz", "Reihen, Konvergenztests", _dt(2025, 11, 11, 18), 4, 1),
        (ds, "DS: DFA-Konstruktion", "DFA, endliche Automaten", _dt(2025, 11, 13, 17), 4, 1),
    ]

    for i, (course, title, topic, taken, correct, false) in enumerate(arc):
        total = correct + false
        questions = [
            {
                "question": f"Frage {j + 1} zu {topic}",
                "answer": "Korrekte Antwort.",
                "options": ["Korrekte Antwort.", "Distraktor A", "Distraktor B", "Distraktor C"],
                "correct_index": 0,
                "topic": topic,
                "source_ref": course.name,
            }
            for j in range(total)
        ]
        quiz = Quiz(
            user_id=user_id,
            course_id=course.id,
            title=title,
            topic=topic,
            estimated_duration_minutes=10,
            questions=questions,
            created_at=taken,
            updated_at=taken,
        )
        session.add(quiz)
        await session.flush()
        result = QuizResult(
            user_id=user_id,
            quiz_id=quiz.id,
            correct_answers=correct,
            false_answers=false,
            quiz_taken_datetime=taken,
        )
        session.add(result)
    await session.flush()
    log.info("seeded %d quizzes + results", len(arc))


_CHAT_ARC: list[tuple[datetime, str, str]] = [
    (_dt(2025, 9, 22, 9, 15), "user", "Hi, I'm Odin. First semester CS at TUM. Bit nervous."),
    (_dt(2025, 9, 22, 9, 16), "system", "Hey Odin, welcome! Let's set you up. Which courses are you taking this term?"),
    (_dt(2025, 9, 22, 9, 18), "user", "Einführung in die Informatik, Diskrete Strukturen, and Analysis für Informatik."),
    (_dt(2025, 9, 22, 9, 18), "system", "Great — all three saved. I'll help you stay on top of them. Shout whenever you want to check in after a lecture or need a quiz."),
    (_dt(2025, 9, 25, 19, 40), "user", "Did my first EInf quiz — 3/5. Rough."),
    (_dt(2025, 9, 25, 19, 41), "system", "First week, totally normal. You came in cold on Python — what tripped you up most?"),
    (_dt(2025, 9, 25, 19, 43), "user", "List slicing and some loop edge cases. I think I get it now after redoing the exercises."),
    (_dt(2025, 9, 25, 19, 44), "system", "Good. Redo is the right move. Let's revisit slicing in a week to see if it stuck."),
    (_dt(2025, 10, 3, 21, 10), "user", "DS lecture today on sets/relations was dense but cool."),
    (_dt(2025, 10, 3, 21, 11), "system", "That one usually clicks in retrospect — the relations stuff comes back everywhere."),
    (_dt(2025, 10, 15, 18, 30), "user", "Analysis exercise session was rough — I'm behind on proof mechanics."),
    (_dt(2025, 10, 15, 18, 31), "system", "Expected — your head start was on CS topics, not Analysis. Closing the gap now beats closing it in January."),
    (_dt(2025, 10, 17, 22, 5), "user", "Low-energy day. Skipped my evening study block."),
    (_dt(2025, 10, 17, 22, 6), "system", "Noted. One skipped block isn't a pattern — rest is part of the plan. See how tomorrow feels."),
    (_dt(2025, 10, 24, 18, 20), "user", "Two-week streak of getting sleep before midnight. Quiz scores noticeably sharper."),
    (_dt(2025, 10, 24, 18, 21), "system", "The correlation is real. Keep protecting the sleep window — it's compounding."),
    (_dt(2025, 11, 3, 20, 0), "user", "DS covered the Chomsky hierarchy today. Heavy but I'm into it."),
    (_dt(2025, 11, 3, 20, 1), "system", "You actually read ahead on Chomsky in the first week of September — that background will pay off when you hit automata."),
    (_dt(2025, 11, 13, 17, 5), "user", "DS lecture clicked today. DFAs and NFAs both. Powerset construction is the next thing to nail."),
    (_dt(2025, 11, 13, 17, 6), "system", "You connected it to the pre-semester Chomsky reading — exactly the kind of carryover that tells me the concept is settling. I'll have a drill ready for Friday."),
    (_dt(2025, 11, 13, 23, 30), "user", "Good night. Tired but happy with today."),
    (_dt(2025, 11, 13, 23, 31), "system", "Rest well. Tomorrow you get the NFA→DFA follow-up — come in fresh."),
]


async def _seed_chat_history(session, user_id: int) -> None:
    for seq, (ts, author, content) in enumerate(_CHAT_ARC, start=1):
        session.add(
            ChatMessage(
                user_id=user_id,
                timestamp=ts,
                author=author,
                sequence_number=seq,
                content=content,
            )
        )
    await session.flush()
    log.info("seeded %d chat messages", len(_CHAT_ARC))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    await create_all_tables()
    async with AsyncSessionLocal() as session:
        user = await _ensure_user(session)
        await _wipe_user_data(session, user.id)
        await session.commit()
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(User).where(User.username == DEMO_USERNAME))
        assert user is not None
        courses = await _seed_courses(session, user.id)
        await _seed_schedule(session, user.id, courses)
        await _seed_quiz_history(session, user.id, courses)
        await _seed_chat_history(session, user.id)
        await session.commit()
    log.info("demo SQL seed complete — user=%s id=%d", DEMO_USERNAME, user.id)


if __name__ == "__main__":
    asyncio.run(main())
