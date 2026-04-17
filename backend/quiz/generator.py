import instructor
from openai import OpenAI
from pydantic import BaseModel, Field

MOCK_SCHEDULE = [
    {
        "course": "Machine Learning",
        "type": "Lecture",
        "day": "Monday",
        "time": "10:00-12:00",
        "topic": "Neural Networks and Backpropagation",
    },
    {
        "course": "Algorithms & Data Structures",
        "type": "Tutorium",
        "day": "Tuesday",
        "time": "14:00-16:00",
        "topic": "Dynamic Programming",
    },
    {
        "course": "Database Systems",
        "type": "Lecture",
        "day": "Wednesday",
        "time": "09:00-11:00",
        "topic": "Query Optimization and Indexes",
    },
    {
        "course": "Software Engineering",
        "type": "Lecture",
        "day": "Thursday",
        "time": "13:00-15:00",
        "topic": "Design Patterns (GoF)",
    },
    {
        "course": "Machine Learning",
        "type": "Tutorium",
        "day": "Friday",
        "time": "11:00-12:00",
        "topic": "Practical: Training CNNs with PyTorch",
    },
]

SYSTEM_PROMPT = """You are StudyBot, an AI study assistant helping university students \
reinforce their learning through quizzes. Your quizzes are concise, pedagogically sound, \
and targeted at the material the student has recently covered. Always make questions clear \
and unambiguous. Distractors (wrong options) should be plausible but clearly wrong to \
someone who studied the material. Include a brief explanation for the correct answer."""


class QuizOption(BaseModel):
    text: str
    is_correct: bool


class QuizQuestion(BaseModel):
    question: str
    options: list[QuizOption] = Field(min_length=3, max_length=5)
    explanation: str = Field(description="Why the correct answer is right")


class Quiz(BaseModel):
    title: str
    topic: str
    estimated_duration_minutes: int = Field(ge=1, le=60)
    questions: list[QuizQuestion] = Field(min_length=3, max_length=15)


def generate_quiz(
    include_schedule: bool = False,
    slide_content: str | None = None,
    num_questions: int = 5,
) -> Quiz:
    client = instructor.from_openai(OpenAI())

    context_parts: list[str] = []

    if include_schedule:
        schedule_text = "\n".join(
            f"- {s['day']} {s['time']}: {s['course']} {s['type']} — {s['topic']}"
            for s in MOCK_SCHEDULE
        )
        context_parts.append(f"Student's weekly schedule:\n{schedule_text}")

    if slide_content:
        context_parts.append(f"Lecture slide content:\n{slide_content}")

    if context_parts:
        user_message = (
            "Generate a quiz based on the following study context.\n\n"
            + "\n\n".join(context_parts)
            + f"\n\nCreate {num_questions} multiple-choice questions."
        )
    else:
        user_message = (
            "Generate a general study skills and academic knowledge quiz "
            f"with {num_questions} multiple-choice questions suitable for a university student."
        )

    quiz = client.chat.completions.create(
        model="gpt-4o-mini",
        response_model=Quiz,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    return quiz
