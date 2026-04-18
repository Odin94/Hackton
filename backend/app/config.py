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
