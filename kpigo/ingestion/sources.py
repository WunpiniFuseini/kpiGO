"""Where a load's rows come from: a registered object, a drop folder or an upload.

Database reads go through ``validator.read_source``, the same code the
standalone validator runs, so the only SQL kpiGo ever sends is a column list
and a registered object name built from validated identifiers.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings

from kpigo.ingestion import crypto
from kpigo.ingestion import validator as v
from kpigo.ingestion.models import Connection, CredentialSecret, Feed

DROP_SUFFIXES = (".csv", ".xlsx")
PROCESSING = ".processing"
PROCESSED = "processed"
QUARANTINED = "quarantined"


def row_cap(connection: Connection | None = None) -> int:
    cap = int(settings.KPIGO_INGEST_ROW_CAP)
    if connection is not None and connection.options.get("row_cap"):
        cap = min(cap, int(connection.options["row_cap"]))
    return cap


def statement_timeout(connection: Connection) -> int:
    limit = int(settings.KPIGO_PULL_TIMEOUT_SECONDS)
    if connection.options.get("statement_timeout_seconds"):
        limit = min(limit, int(connection.options["statement_timeout_seconds"]))
    return limit


def spec_for(connection: Connection) -> v.SourceSpec:
    password = ""
    if connection.secret_ref is not None:
        secret = CredentialSecret.objects.filter(
            org_id=connection.org_id, secret_id=connection.secret_ref
        ).first()
        if secret is not None:
            password = crypto.decrypt(secret.ciphertext)
    return v.SourceSpec(
        driver=connection.driver,
        host=connection.host,
        port=connection.port,
        database=connection.database,
        username=connection.username,
        password=password,
        statement_timeout_seconds=statement_timeout(connection),
    )


def pull(feed: Feed) -> v.RawTable:
    connection = feed.connection
    if connection is None or not feed.source_object:
        raise v.SourceError("This feed has no connection or source object.")
    if connection.status != "active":
        raise v.SourceError(f"Connection '{connection.name}' is disabled.")
    return v.read_source(
        spec_for(connection),
        feed.source_object,
        v.TEMPLATES[feed.template_name],
        row_cap=row_cap(connection),
    )


# ── drop folders ─────────────────────────────────────────────────────────────


def drop_root() -> Path:
    return Path(str(settings.KPIGO_DROP_ROOT)).resolve()


def safe_relative(path: str) -> str:
    """A relative path with no way out of where it is joined: no '..', no absolute."""
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise ValueError(f"'{path}' must be a relative path inside the drop root.")
    return candidate.as_posix()


def drop_dir(feed: Feed) -> Path:
    if not feed.drop_path:
        raise v.SourceError("This feed has no drop folder.")
    root = drop_root()
    folder = (root / safe_relative(feed.drop_path)).resolve()
    if root != folder and root not in folder.parents:
        raise v.SourceError("The drop folder resolves outside the drop root.")
    return folder


def drop_file(feed: Feed, name: str) -> Path:
    folder = drop_dir(feed)
    path = (folder / safe_relative(name)).resolve()
    if folder not in path.parents:
        raise v.SourceError(f"'{name}' is not inside this feed's drop folder.")
    if path.suffix.lower() not in DROP_SUFFIXES:
        raise v.SourceError(f"'{name}' is not a .csv or .xlsx file.")
    if not path.is_file():
        raise v.SourceError(f"No file '{name}' in the drop folder.")
    return path


def read_drop(feed: Feed, name: str) -> v.RawTable:
    path = drop_file(feed, name)
    return v.read_file(path.name, path.read_bytes(), row_cap=row_cap())


def settled_files(feed: Feed, now: float, settle_seconds: int) -> list[Path]:
    """Files waiting in the folder (not in a subfolder) that have stopped changing."""
    folder = drop_dir(feed)
    if not folder.is_dir():
        return []
    found = [
        p
        for p in folder.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() in DROP_SUFFIXES
        and now - p.stat().st_mtime >= settle_seconds
    ]
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name))


def claim(feed: Feed, path: Path) -> str:
    """Move a waiting file into the processing folder so no later tick picks it up again."""
    target_dir = drop_dir(feed) / PROCESSING
    target_dir.mkdir(exist_ok=True)
    target = target_dir / path.name
    path.replace(target)
    return f"{PROCESSING}/{path.name}"


def file_away(feed: Feed, name: str, outcome_ok: bool) -> None:
    """After a live load: processed/ for a good one, quarantined/ for the rest."""
    try:
        path = drop_file(feed, name)
    except v.SourceError:
        return
    target_dir = drop_dir(feed) / (PROCESSED if outcome_ok else QUARANTINED)
    target_dir.mkdir(exist_ok=True)
    path.replace(target_dir / path.name)
