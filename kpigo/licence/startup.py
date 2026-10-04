"""Which modules register when the process starts.

Read from the licence file ``licence.activate`` writes, because the database is
not consulted while apps load. The file only selects modules: whether the
licence is valid for this install, and its grace state, are checked against the
database row on every invocation (``kpigo.licence.gate``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

from kpigo.licence.document import InvalidLicence, verify

logger = logging.getLogger("kpigo.licence")

# The modules this process registered, for the health page's restart check.
registered_modules: tuple[str, ...] = ()


def entitled_modules() -> list[str]:
    global registered_modules
    modules = _from_file()
    if modules is None:
        enforced = bool(getattr(settings, "KPIGO_LICENCE_ENFORCED", True))
        modules = [] if enforced else list(settings.KPIGO_ENTITLED_MODULES)
    registered_modules = tuple(sorted(modules))
    return modules


def _from_file() -> list[str] | None:
    path = getattr(settings, "KPIGO_LICENCE_FILE", None)
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("licence file unreadable: %s", exc)
        return None
    try:
        return list(verify(text).modules)
    except InvalidLicence as exc:
        logger.warning("licence file rejected: %s", exc)
        return None
