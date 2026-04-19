"""SQLAlchemy async engine, session factory, and declarative base."""

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "agent_memory.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)

# expire_on_commit=False keeps objects usable after session.commit()
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def _sqlite_column_names(conn: AsyncConnection, table: str) -> set[str]:
    rows = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in rows.fetchall()}


async def _sqlite_add_column_if_missing(
    conn: AsyncConnection, table: str, column: str, ddl: str
) -> None:
    columns = await _sqlite_column_names(conn, table)
    if column in columns:
        return
    await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    log.info("DB migration added column %s.%s", table, column)


async def create_all_tables() -> None:
    """Create all tables that are not yet present. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await _sqlite_add_column_if_missing(
                conn,
                "users",
                "interests",
                "interests VARCHAR(2048)",
            )
            await _sqlite_add_column_if_missing(
                conn,
                "users",
                "future_goals",
                "future_goals VARCHAR(2048)",
            )
            await _sqlite_add_column_if_missing(
                conn,
                "schedule_events",
                "course_id",
                "course_id INTEGER REFERENCES courses(id)",
            )
            await _sqlite_add_column_if_missing(
                conn,
                "quizzes",
                "course_id",
                "course_id INTEGER REFERENCES courses(id)",
            )
    log.info("DB tables verified at %s", DB_PATH)
