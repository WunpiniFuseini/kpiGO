"""The action registry and auto-discovery from ``kpigo/*/actions/*.py`` (TDD §3.3)."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType

from kpigo.action.definition import ActionDefinition, definition_of
from kpigo.action.errors import RegistryError, UnknownAction

KPIGO_ROOT = Path(__file__).resolve().parent.parent


class Registry:
    """Actions keyed by name. Adapters read this; nothing reaches a handler any other way."""

    def __init__(self) -> None:
        self._actions: dict[str, ActionDefinition] = {}
        self._skipped: dict[str, ActionDefinition] = {}

    def register(
        self, definition: ActionDefinition, *, entitled: Iterable[str] | None = None
    ) -> bool:
        """Register an action unless its module is not entitled.

        Platform actions always register. A module the licence does not carry
        registers nothing, so there is no half-enabled state. Returns whether the
        action was registered.
        """
        existing = self._actions.get(definition.name)
        if existing is not None and existing.handler is not definition.handler:
            raise RegistryError(f"Action '{definition.name}' is registered twice.")
        if (
            entitled is not None
            and definition.module != "platform"
            and definition.module not in set(entitled)
        ):
            self._skipped[definition.name] = definition
            return False
        self._actions[definition.name] = definition
        return True

    def register_module(self, module: ModuleType, *, entitled: Iterable[str] | None = None) -> None:
        for _, obj in inspect.getmembers(module, callable):
            definition = definition_of(obj)
            if definition is not None and obj.__module__ == module.__name__:
                self.register(definition, entitled=entitled)

    def unregister(self, name: str) -> None:
        self._actions.pop(name, None)
        self._skipped.pop(name, None)

    def get(self, name: str) -> ActionDefinition:
        try:
            return self._actions[name]
        except KeyError:
            raise UnknownAction(f"No action named '{name}' is registered.") from None

    def find(self, name: str) -> ActionDefinition | None:
        return self._actions.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._actions

    def __iter__(self) -> Iterator[ActionDefinition]:
        return iter(sorted(self._actions.values(), key=lambda d: d.name))

    def __len__(self) -> int:
        return len(self._actions)

    @property
    def skipped(self) -> list[str]:
        """Actions discovered but not registered because their module is unlicensed."""
        return sorted(self._skipped)

    def clear(self) -> None:
        self._actions.clear()
        self._skipped.clear()


def discover_modules(root: Path = KPIGO_ROOT, package: str = "kpigo") -> list[str]:
    """Dotted names of every ``kpigo/<app>/actions/<module>.py``."""
    found: list[str] = []
    for app_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        actions_dir = app_dir / "actions"
        if not (actions_dir / "__init__.py").exists():
            continue
        for info in pkgutil.iter_modules([str(actions_dir)]):
            if not info.ispkg:
                found.append(f"{package}.{app_dir.name}.actions.{info.name}")
    return found


def autodiscover(target: Registry, *, entitled: Iterable[str] | None = None) -> Registry:
    entitled_set = set(entitled) if entitled is not None else None
    for dotted in discover_modules():
        target.register_module(importlib.import_module(dotted), entitled=entitled_set)
    return target


registry = Registry()
