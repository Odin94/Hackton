"""
Agent harness
=============
Provides ``_quiz_llm_call``, the single entry-point for all LLM interactions in
the agent layer. The model is handed persistence and notification tools; if it
fires a tool the harness executes it and feeds the result back so the model can
decide whether to call another tool or finish.

Returns the full conversation as ``list[dict]`` (one dict per message).
"""

import json
import logging
from datetime import UTC, datetime

import litellm

from .db import create_notification, write_entry

logger = logging.getLogger(__name__)

# Change to any litellm-compatible model string, e.g. "claude-3-5-haiku-20241022"
MODEL = "gpt-4o-mini"

# ---------------------------------------------------------------------------
# Tool schema exposed to the LLM
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "schedule_notification",
            "description": (
                "Queue a proactive chat notification for a specific user at a specific "
                "time when a reminder, quiz nudge, or study-habit intervention would help."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "The app user who should receive the notification.",
                    },
                    "target_datetime": {
                        "type": "string",
                        "description": "ISO-8601 timestamp for when the notification should be delivered.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The exact message to deliver in chat.",
                    },
                },
                "required": ["user_id", "target_datetime", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_to_db",
            "description": (
                "Persist a structured note, insight, or observation to the agent "
                "memory database so it can be referenced in future sessions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_type": {
                        "type": "string",
                        "description": (
                            "Short category label, e.g. 'quiz_insight', "
                            "'study_pattern', 'reminder', 'flagged_topic'."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text of the note to store.",
                    },
                },
                "required": ["entry_type", "content"],
            },
        },
    }
]


# ---------------------------------------------------------------------------
# Internal tool dispatcher
# ---------------------------------------------------------------------------

async def _dispatch_tool(name: str, args: dict) -> str:
    if name == "schedule_notification":
        user_id = int(args["user_id"])
        target_datetime = datetime.fromisoformat(str(args["target_datetime"]).replace("Z", "+00:00"))
        if target_datetime.tzinfo is None:
            target_datetime = target_datetime.replace(tzinfo=UTC)
        notification_id = await create_notification(
            user_id,
            str(args["content"]),
            target_datetime,
        )
        return (
            f"Scheduled notification #{notification_id} for user {user_id} at "
            f"{target_datetime.isoformat()}."
        )
    if name == "write_to_db":
        row_id = await write_entry(args["entry_type"], args["content"])
        return f"Stored entry #{row_id} (type='{args['entry_type']}')."
    return f"Unknown tool '{name}' — no action taken."


# ---------------------------------------------------------------------------
# Public LLM call
# ---------------------------------------------------------------------------

async def _quiz_llm_call(system_prompt: str, user_prompt: str) -> list[dict]:
    """
    Run one agentic turn.

    1. Sends *system_prompt* + *user_prompt* to the LLM together with the
       ``write_to_db`` tool definition.
    2. If the model returns tool calls, executes them and feeds results back.
    3. Repeats until the model produces a plain text reply (no tool calls).

    Returns the full conversation as a list of message dicts.
    """
    logger.debug(
        "_quiz_llm_call start model=%s system_len=%d user_len=%d",
        MODEL, len(system_prompt), len(user_prompt),
    )
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    turn = 0

    while True:
        turn += 1
        logger.debug("_quiz_llm_call LLM request turn=%d messages_in_history=%d", turn, len(messages))
        response = await litellm.acompletion(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        assistant_msg = response.choices[0].message
        finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
        logger.debug(
            "_quiz_llm_call LLM response turn=%d finish_reason=%s content_len=%d",
            turn, finish_reason, len(assistant_msg.content or ""),
        )

        # Append the raw assistant message to history
        messages.append(assistant_msg.model_dump(exclude_none=True))

        tool_calls = getattr(assistant_msg, "tool_calls", None) or []
        if not tool_calls:
            # Model finished — return full conversation
            logger.debug(
                "_quiz_llm_call done in %d turns, final reply: %.120s",
                len(messages),
                assistant_msg.content or "",
            )
            return messages

        logger.debug("_quiz_llm_call turn=%d executing %d tool call(s)", turn, len(tool_calls))
        # Execute each tool call and inject results before the next LLM turn
        for tc in tool_calls:
            args: dict = json.loads(tc.function.arguments)
            logger.info("Tool call → %s(%s)", tc.function.name, args)
            result = await _dispatch_tool(tc.function.name, args)
            logger.debug("Tool result tool=%s result=%.120s", tc.function.name, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )
