from collections.abc import Callable, Iterator
from typing import Any

import pytest
from django.contrib.auth.models import Group, User

from kpigo.action import registry
from kpigo.action.definition import ActionDefinition


@pytest.fixture
def make_user(db: None) -> Callable[..., User]:
    counter = iter(range(10_000))

    def make(*roles: str, username: str | None = None, **extra: Any) -> User:
        user = User.objects.create_user(username=username or f"user{next(counter)}", **extra)
        for role in roles:
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        return user

    return make


@pytest.fixture
def register() -> Iterator[Callable[[Callable[..., object]], ActionDefinition]]:
    """Register a test-only action in the global registry for the duration of a test."""
    added: list[str] = []

    def add(fn: Callable[..., object]) -> ActionDefinition:
        definition: ActionDefinition = fn.__kpigo_action__  # type: ignore[attr-defined]
        registry.register(definition)
        added.append(definition.name)
        return definition

    yield add
    for name in added:
        registry.unregister(name)
