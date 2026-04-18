import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str
    llm_model: str = "openrouter/openai/gpt-4o-mini"
    data_root_directory: str = str(_BACKEND_DIR / ".cognee_system")
    # Wall-clock cap on our own LiteLLM quiz call. Distinct from any cognee-internal
    # LLM_TIMEOUT env var — kept separate so a change here can't fight cognee's
    # own retry/timeout machinery.
    llm_call_timeout_seconds: float = 30.0


settings = Settings()

# cognee reads DATA_ROOT_DIRECTORY / SYSTEM_ROOT_DIRECTORY at import time and requires
# absolute paths. Normalize and pin to backend/ so data survives venv rebuilds.
_abs = Path(settings.data_root_directory).expanduser().resolve()
os.environ["DATA_ROOT_DIRECTORY"] = str(_abs / "data")
os.environ["SYSTEM_ROOT_DIRECTORY"] = str(_abs / "system")
os.environ["CACHE_ROOT_DIRECTORY"] = str(_abs / "cache")

# cognee doesn't auto-create these directories on first run — missing dirs
# surface as cryptic "unable to open database file" SQLite errors. Create them
# here so the first ingest/cognify call works on a fresh VPS.
for _sub in (_abs / "data", _abs / "system" / "databases", _abs / "cache"):
    _sub.mkdir(parents=True, exist_ok=True)

# Claude Code's shell env sets ALL_PROXY=socks5h://... which httpx tries to
# use, but socksio isn't installed in the cognee dep tree. HTTPS_PROXY (the
# HTTP proxy) is still available and works for OpenAI/OpenRouter calls —
# drop the SOCKS entries so httpx falls through to HTTPS_PROXY.
for _k in ("ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)


# SQLite "too many SQL variables" fix: cognee's upsert_edges is patched directly
# in the venv at cognee/modules/graph/methods/upsert_edges.py to split large
# edge inserts into 2 000-row sub-batches (11 cols × 2 000 = 22 000 placeholders,
# well under SQLite's 32 766 limit). See iter-log iter 17 for context.
