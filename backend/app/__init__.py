"""Importing this package loads `app.config`, which mutates
`DATA_ROOT_DIRECTORY` / `SYSTEM_ROOT_DIRECTORY` / `CACHE_ROOT_DIRECTORY`
before any downstream module imports cognee. This guards against ruff's
import sorter reordering `from app.config import settings` below `import
cognee` inside individual modules, which would leave cognee's cached
`base_config` pointing at defaults inside the venv."""
from app import config as _config  # noqa: F401
