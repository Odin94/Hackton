from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.database import Base
from agent.models import Course, Quiz, QuizResult, User
from app import routes_auth, routes_demo
from app.routes_auth import router as auth_router
from app.routes_demo import router as demo_router


def _question(prompt: str, correct_index: int = 0) -> dict:
    return {
        "question": prompt,
        "answer": "Because the materials say so.",
        "options": ["A", "B", "C", "D"],
        "correct_index": correct_index,
        "topic": "transformers",
        "source_ref": "Machine Learning",
    }


def test_demo_quiz_library_lists_stats_and_retake_persists_result(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _seed() -> tuple[int, int]:
        async with factory() as session:
            user = User(username="quiz_demo_user")
            session.add(user)
            await session.flush()

            course = Course(user_id=user.id, name="Machine Learning")
            session.add(course)
            await session.flush()

            weak_quiz = Quiz(
                user_id=user.id,
                course_id=course.id,
                title="Quiz: Attention recap",
                topic="transformers",
                estimated_duration_minutes=6,
                questions=[
                    {
                        "question": "What does attention do?",
                        "options": ["A", "B", "C", "D"],
                        "correct_index": 0,
                        "source_ref": "Machine Learning",
                    },
                    _question("Why positions?"),
                ],
            )
            strong_quiz = Quiz(
                user_id=user.id,
                course_id=course.id,
                title="Quiz: Gradient descent",
                topic="optimization",
                estimated_duration_minutes=4,
                questions=[_question("What is a gradient?")],
            )
            session.add_all([weak_quiz, strong_quiz])
            await session.flush()

            session.add_all(
                [
                    QuizResult(
                        user_id=user.id,
                        quiz_id=weak_quiz.id,
                        correct_answers=1,
                        false_answers=3,
                        quiz_taken_datetime=weak_quiz.created_at,
                    ),
                    QuizResult(
                        user_id=user.id,
                        quiz_id=strong_quiz.id,
                        correct_answers=4,
                        false_answers=0,
                        quiz_taken_datetime=strong_quiz.created_at,
                    ),
                ]
            )
            await session.commit()
            return user.id, weak_quiz.id

    asyncio.run(_init())

    monkeypatch.setattr(routes_auth, "AsyncSessionLocal", factory)
    monkeypatch.setattr(routes_demo, "AsyncSessionLocal", factory, raising=False)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(demo_router)
    client = TestClient(app)

    user_id, weak_quiz_id = asyncio.run(_seed())

    login = client.post("/login", json={"username": "quiz_demo_user"})
    assert login.status_code == 200
    token = login.json()["token"]

    library = client.get("/demo/quizzes", headers={"Authorization": f"Bearer {token}"})
    assert library.status_code == 200
    body = library.json()
    assert body["overall_average_percent"] == 62
    weak_quiz = next(item for item in body["quizzes"] if item["id"] == weak_quiz_id)
    assert weak_quiz["average_percent"] == 25
    assert weak_quiz["underperformed"] is True
    assert weak_quiz["question_count"] == 2
    assert len(weak_quiz["questions"]) == 2
    assert weak_quiz["questions"][0]["answer"] == "Review the lecture materials for the rationale."
    assert weak_quiz["questions"][0]["topic"] == "transformers"

    retake = client.post(
        f"/demo/quizzes/{weak_quiz_id}/retake",
        json={"correct_answers": 2, "false_answers": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert retake.status_code == 200
    retake_body = retake.json()
    assert retake_body["quiz_id"] == weak_quiz_id
    assert retake_body["latest_percent"] == 100
    assert retake_body["average_percent"] == 62
    assert retake_body["attempt_count"] == 2

    async def _count_results() -> int:
        async with factory() as session:
            rows = await session.execute(
                select(QuizResult).where(QuizResult.user_id == user_id, QuizResult.quiz_id == weak_quiz_id)
            )
            return len(list(rows.scalars().all()))

    assert asyncio.run(_count_results()) == 2
    asyncio.run(engine.dispose())
