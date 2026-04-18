"""Demo-clock monkey-patch.

When ``CURRENT_DATE_OVERRIDE`` is set (ISO-8601 string), replace ``datetime``
in every already-imported backend module with a subclass whose ``now()`` and
``utcnow()`` return the frozen instant. Every ``from datetime import datetime``
becomes a module-level attribute, so rebinding that attribute redirects all
subsequent ``datetime.now(...)`` calls inside the module.

Limitation: only patches modules already present in ``sys.modules`` when called.
Must run after the app's module tree is imported but before any request-time
``datetime.now()`` call. See ``main.py`` for the call site.
"""
from __future__ import annotations

import datetime as _dt
import logging
import sys
from datetime import datetime

log = logging.getLogger(__name__)

_PATCH_PREFIXES = ("app.", "agent.")
_PATCH_EXACT = {"app", "agent"}


def _make_frozen_class(fixed: datetime) -> type[datetime]:
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            if tz is None:
                return fixed.replace(tzinfo=None)
            return fixed.astimezone(tz)

        @classmethod
        def utcnow(cls):  # type: ignore[override]
            return fixed.astimezone(_dt.UTC).replace(tzinfo=None)

    _FrozenDateTime.__name__ = "FrozenDateTime"
    return _FrozenDateTime


def install_demo_clock(override: str | None) -> None:
    if not override:
        return
    try:
        fixed = datetime.fromisoformat(override)
    except ValueError as e:
        log.error("CURRENT_DATE_OVERRIDE invalid ISO string %r: %s", override, e)
        return
    if fixed.tzinfo is None:
        fixed = fixed.replace(tzinfo=_dt.UTC)

    frozen_cls = _make_frozen_class(fixed)
    patched: list[str] = []
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        if name in _PATCH_EXACT or any(name.startswith(p) for p in _PATCH_PREFIXES):
            if getattr(module, "datetime", None) is datetime:
                module.datetime = frozen_cls
                patched.append(name)
    log.warning(
        "Demo clock ACTIVE — datetime.now() frozen at %s in %d modules: %s",
        fixed.isoformat(),
        len(patched),
        ", ".join(patched),
    )
