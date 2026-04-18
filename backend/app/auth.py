"""Minimal in-memory token store.

Not persistent — tokens are lost on restart, which is fine for a local demo.
Tokens are random UUIDs; no JWTs, no signing, no expiry.
"""

import logging
import uuid

log = logging.getLogger(__name__)

# token (str uuid4) -> user_id
_tokens: dict[str, int] = {}


def create_token(user_id: int) -> str:
    """Mint a fresh token for *user_id* and remember it."""
    token = str(uuid.uuid4())
    _tokens[token] = user_id
    log.debug("Token minted for user_id=%d", user_id)
    return token


def get_user_id(token: str) -> int | None:
    """Resolve *token* → user_id, or None if the token is unknown."""
    return _tokens.get(token)


def revoke(token: str) -> None:
    """Forget a token (e.g. on explicit logout)."""
    _tokens.pop(token, None)
