"""Background scheduler — two concurrent asyncio loops.

LLM check-in loop (every 1 h)
    Asks the model whether there is anything worth persisting to the agent log.
    The model can call the ``write_to_db`` tool freely.

Notification dispatch loop (every 60 s)
    Queries the DB for pending Notifications whose target_datetime has passed
    and delivers them (WebSocket send is currently mocked — see quiz_workflow.py).

Both loops are started from ``start_scheduler()``, which returns a pair of Tasks
so the caller can cancel them cleanly on shutdown.
"""

import asyncio
import logging

from .db import read_recent
from .harness import _quiz_llm_call
from .quiz_workflow import dispatch_due_notifications

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

LLM_CHECKIN_INTERVAL = 3600  # 1 hour
NOTIFICATION_DISPATCH_INTERVAL = 60  # 1 minute

# ---------------------------------------------------------------------------
# LLM check-in loop
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
You are StudyBot's autonomous memory agent.  You run in the background and
decide what is worth remembering for future study sessions.

You have access to a write_to_db tool.  Use it to store quiz insights, study
patterns, recurring weak topics, or any other observation that would be useful
to surface later.  Be selective — only write what is genuinely valuable.
"""


def _build_llm_user_prompt(recent: list[dict]) -> str:
    if recent:
        rows = "\n".join(
            f"  [{r['created_at']}] ({r['entry_type']}) {r['content'][:120]}"
            for r in recent
        )
        context = f"Most recent agent-log entries (newest first):\n{rows}"
    else:
        context = "The agent log is currently empty — nothing has been recorded yet."

    return (
        "It's your scheduled hourly check-in.\n\n"
        f"{context}\n\n"
        "Based on what you know about recent quiz activity and student learning "
        "patterns, decide whether there is anything new and meaningful worth "
        "recording.  If yes, call write_to_db.  If not, say so briefly."
    )


async def _llm_checkin_loop() -> None:
    log.info("LLM check-in loop started (interval=%ds)", LLM_CHECKIN_INTERVAL)
    while True:
        await asyncio.sleep(LLM_CHECKIN_INTERVAL)
        log.info("Scheduler: LLM check-in starting")
        try:
            recent = await read_recent(limit=10)
            log.debug("Scheduler: LLM check-in fetched %d recent log entries", len(recent))
            messages = await _quiz_llm_call(_LLM_SYSTEM_PROMPT, _build_llm_user_prompt(recent))
            log.debug("Scheduler: LLM check-in conversation turns=%d", len(messages))
            final = next(
                (m.get("content") for m in reversed(messages) if m.get("role") == "assistant"),
                "(no reply)",
            )
            log.info("Scheduler: LLM check-in done — %s", (final or "")[:200])
        except Exception:
            log.exception("Scheduler: LLM check-in failed")


# ---------------------------------------------------------------------------
# Notification dispatch loop
# ---------------------------------------------------------------------------


async def _notification_dispatch_loop() -> None:
    log.info("Notification dispatch loop started (interval=%ds)", NOTIFICATION_DISPATCH_INTERVAL)
    while True:
        await asyncio.sleep(NOTIFICATION_DISPATCH_INTERVAL)
        log.debug("Scheduler: notification dispatch tick")
        try:
            n = await dispatch_due_notifications()
            if n:
                log.info("Scheduler: dispatched %d notification(s)", n)
            else:
                log.debug("Scheduler: no due notifications this tick")
        except Exception:
            log.exception("Scheduler: notification dispatch failed")


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def start_scheduler() -> tuple[asyncio.Task, asyncio.Task]:
    """Spawn both background loops.

    Must be called from inside a running event loop (e.g. a FastAPI lifespan).
    Returns ``(llm_task, dispatch_task)`` so callers can cancel on shutdown::

        llm_task, dispatch_task = start_scheduler()
        ...
        llm_task.cancel()
        dispatch_task.cancel()
    """
    llm_task = asyncio.create_task(_llm_checkin_loop(), name="agent-llm-checkin")
    dispatch_task = asyncio.create_task(
        _notification_dispatch_loop(), name="agent-notification-dispatch"
    )
    return llm_task, dispatch_task
