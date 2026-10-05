"""The action API's OpenAPI document: what the frontend's types are generated from."""

from __future__ import annotations

import json
import subprocess
import sys
import typing
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from kpigo.action import registry
from kpigo.urls import api

ROOT = Path(__file__).resolve().parent.parent


def _models() -> dict[str, set[str]]:
    """Every Pydantic model an action takes or returns, nested ones included, by class name."""
    found: dict[str, set[str]] = defaultdict(set)
    seen: set[type] = set()

    def walk(annotation: object) -> None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in seen:
                return
            seen.add(annotation)
            found[annotation.__name__].add(f"{annotation.__module__}.{annotation.__qualname__}")
            for field in annotation.model_fields.values():
                walk(field.annotation)
            return
        for arg in typing.get_args(annotation):
            walk(arg)

    for definition in registry:
        walk(definition.schema)
        walk(definition.output)
    return found


def test_model_names_are_unique() -> None:
    # The OpenAPI document names schemas by class name, so two models called
    # ListOut silently become one and the frontend gets the wrong type.
    clashes = {name: sorted(paths) for name, paths in _models().items() if len(paths) > 1}
    assert not clashes, f"Rename these so each is unique across actions: {clashes}"


def test_every_route_names_its_action() -> None:
    schema = api.get_openapi_schema(path_prefix="/api/v1")
    named = {op["x-kpigo-action"] for item in schema["paths"].values() for op in item.values()}
    assert named == {d.name for d in registry}


def test_frontend_copy_is_current() -> None:
    # frontend/openapi.json feeds `npm run gen:api`; regenerate both when an action changes.
    exported = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_openapi.py")],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    committed = (ROOT / "frontend" / "openapi.json").read_text()
    assert json.loads(exported) == json.loads(committed), (
        "frontend/openapi.json is out of date: run "
        "`uv run python scripts/export_openapi.py frontend/openapi.json` and "
        "`npm run gen:api` in frontend/."
    )
