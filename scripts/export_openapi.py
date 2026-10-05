"""Write the action API's OpenAPI document for the frontend's generated types.

    uv run python scripts/export_openapi.py frontend/openapi.json

The frontend's request and response types come from this file
(``npm run gen:api``), so a renamed field is a type error in the UI, not a
blank screen. CI regenerates both and fails on any difference.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "kpigo.settings")
# Every module's actions, whatever this machine's licence says: the UI is built once.
os.environ.setdefault("KPIGO_TESTING", "1")


def main() -> None:
    import django

    django.setup()
    from kpigo.urls import api

    schema = api.get_openapi_schema(path_prefix="/api/v1")
    text = json.dumps(schema, indent=2, sort_keys=True, default=str) + "\n"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if target is None:
        sys.stdout.write(text)
    else:
        target.write_text(text)


if __name__ == "__main__":
    main()
