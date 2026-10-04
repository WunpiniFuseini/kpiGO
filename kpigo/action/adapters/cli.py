"""CLI adapter: ``python manage.py action <name> --user <username> --field value``.

Field values are strings handed to the action's Pydantic schema, which coerces
them exactly as it would an HTTP query string. Values that look like JSON lists
or objects are parsed first, and ``--json '{...}'`` supplies the whole payload.
"""

from __future__ import annotations

import json
from typing import Any

from kpigo.action.errors import InvalidInput


def parse_field_args(tokens: list[str]) -> dict[str, Any]:
    """Turn ``['--subject-id', 'S1', '--flag=true']`` into ``{'subject_id': 'S1', 'flag': 'true'}``."""
    params: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--") or token == "--":
            raise InvalidInput(f"Unexpected argument '{token}'; expected --field value.")
        name, sep, value = token[2:].partition("=")
        if not sep:
            if i + 1 >= len(tokens) or tokens[i + 1].startswith("--"):
                raise InvalidInput(f"Missing value for --{name}.")
            value = tokens[i + 1]
            i += 1
        params[name.replace("-", "_")] = _coerce(value)
        i += 1
    return params


def _coerce(value: str) -> Any:
    stripped = value.strip()
    if stripped[:1] in ("[", "{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value
