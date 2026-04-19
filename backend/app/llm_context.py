from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def current_llm_datetime_context() -> str:
    return f"Current UTC datetime: {datetime.now(UTC).isoformat()}"


def with_current_datetime_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stamped_messages = [dict(message) for message in messages]
    context = current_llm_datetime_context()
    if stamped_messages and stamped_messages[0].get("role") == "system":
        first_message = dict(stamped_messages[0])
        original_content = str(first_message.get("content", "") or "")
        first_message["content"] = f"{context}\n\n{original_content}" if original_content else context
        stamped_messages[0] = first_message
        return stamped_messages
    return [{"role": "system", "content": context}, *stamped_messages]
