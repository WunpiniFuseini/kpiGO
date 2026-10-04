"""The licence check in the action pipeline.

Registration is the first line: an unlicensed module's actions never register.
This is the second, at invoke time: the grace state, and a module the installed
licence no longer carries (activated since the process started).
"""

from __future__ import annotations

from django.conf import settings

from kpigo.action.definition import ActionDefinition
from kpigo.action.errors import LicenceRestricted
from kpigo.licence.state import current

# Always reachable, whatever the licence state: signing in and out, the licence
# screen itself, first-run setup and the health page.
ALWAYS_AVAILABLE_PREFIXES = ("auth.", "licence.", "setup.")
ALWAYS_AVAILABLE = frozenset({"system.health"})


def always_available(name: str) -> bool:
    return name in ALWAYS_AVAILABLE or name.startswith(ALWAYS_AVAILABLE_PREFIXES)


def check(definition: ActionDefinition) -> None:
    if always_available(definition.name):
        return
    state = current(str(settings.KPIGO_ORG_ID))
    detail = {"licence_state": state.state}
    if state.mode == "locked":
        raise LicenceRestricted(state.message, detail=detail)
    if state.mode == "read_only" and not definition.read_only:
        raise LicenceRestricted(state.message, detail=detail)
    if (
        state.licence is not None
        and definition.module != "platform"
        and definition.module not in state.modules
    ):
        raise LicenceRestricted(
            f"The licence does not include the {definition.module} module.", detail=detail
        )
