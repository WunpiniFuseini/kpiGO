"""Read the measures out of a Power BI model, for the import assistant (R6).

A Power BI model carries its KPIs as *measures* — named DAX expressions on a table.
They are what a bank's dashboard actually reports, so they are what kpiGo proposes as
metrics. This module extracts them from the three shapes a model arrives in, all
without the Power BI runtime:

- ``.pbit`` (a template): a ZIP whose ``DataModelSchema`` part is the Tabular model as
  UTF-16 JSON. A template carries the model but no data, so it is the shape to ask a
  client for ("Save as → Power BI template").
- ``model.bim`` / a ``.json`` model: the same Tabular model (TMSL) as a JSON file.
- ``.tmdl`` text, or a PBIP project zipped: the model as TMDL, Power BI's text format.

It is pure: standard library only (``zipfile``, ``json``, ``re``), no kpiGo or Django
imports and nothing executed from the file. A raw ``.pbix`` is not handled — its model
is XPress9-compressed; the client saves it as a ``.pbit`` template instead.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

# Bounds against a hostile upload: a zip that unpacks to far more than it claims, or one
# with a huge number of parts. The caller also size-limits the upload itself.
MAX_UNCOMPRESSED = 64 * 1024 * 1024
MAX_ENTRIES = 10_000


class PowerBiError(ValueError):
    """The file is not a Power BI model this assistant can read."""


@dataclass
class Measure:
    table: str
    name: str
    expression: str = ""
    format_string: str = ""
    display_folder: str = ""
    description: str = ""
    is_hidden: bool = False


@dataclass
class Model:
    source: str  # "pbit" | "bim" | "tmdl"
    tables: list[str] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)


def _as_text(value: object) -> str:
    """A TMSL expression/description is a string or an array of lines; join either."""
    if isinstance(value, list):
        return "\n".join(str(v) for v in value)
    return "" if value is None else str(value)


# ── TMSL (model.bim and .pbit DataModelSchema) ──────────────────────────────


def parse_tmsl(obj: dict[str, Any]) -> Model:
    model = obj.get("model") if isinstance(obj.get("model"), dict) else obj
    tables = model.get("tables") if isinstance(model, dict) else None
    if not isinstance(tables, list):
        raise PowerBiError("No tables in the model; this does not look like a Power BI model.")
    out = Model(source="tmsl")
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_name = str(table.get("name", ""))
        out.tables.append(table_name)
        for m in table.get("measures", []) or []:
            if not isinstance(m, dict) or not m.get("name"):
                continue
            out.measures.append(
                Measure(
                    table=table_name,
                    name=str(m["name"]),
                    expression=_as_text(m.get("expression")),
                    format_string=str(m.get("formatString", "") or ""),
                    display_folder=str(m.get("displayFolder", "") or ""),
                    description=_as_text(m.get("description")),
                    is_hidden=bool(m.get("isHidden", False)),
                )
            )
    return out


def read_bim(data: bytes) -> Model:
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PowerBiError(f"The model file is not valid JSON ({exc}).") from None
    if not isinstance(obj, dict):
        raise PowerBiError("The model file is not a JSON object.")
    model = parse_tmsl(obj)
    model.source = "bim"
    return model


# ── TMDL (PBIP projects, .tmdl files) ───────────────────────────────────────

# `measure 'Revenue' = ...`, `measure "Revenue" = ...`, or `measure Revenue = ...`.
_MEASURE_RE = re.compile(r"""^\s*measure\s+(?:'([^']+)'|"([^"]+)"|([^\s=]+))\s*=\s*(.*)$""")
_PROPERTY_RE = re.compile(r"^\s*([A-Za-z]+):\s*(.*)$")
# A line that opens a new object, ending the current measure's indented block.
_BLOCK_RE = re.compile(r"^\s*(measure|column|table|partition|hierarchy|relationship)\b")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def parse_tmdl(text: str, table: str = "") -> list[Measure]:
    """Pull measures out of TMDL text, best-effort: the name, the expression and the
    formatString property, which is what the proposal needs."""
    measures: list[Measure] = []
    lines = text.splitlines()
    current_table = table
    i = 0
    while i < len(lines):
        line = lines[i]
        table_match = re.match(r"^\s*table\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))", line)
        if table_match:
            current_table = next(g for g in table_match.groups() if g is not None)
            i += 1
            continue
        match = _MEASURE_RE.match(line)
        if not match:
            i += 1
            continue
        name = next(g for g in match.groups()[:3] if g is not None)
        expr_parts = [match.group(4).strip()] if match.group(4).strip() else []
        measure = Measure(table=current_table, name=name.strip())
        base_indent = _indent(line)
        i += 1
        # Consume the measure's indented block: its properties and any wrapped expression.
        while i < len(lines):
            nxt = lines[i]
            if nxt.strip() and _indent(nxt) <= base_indent and not _PROPERTY_RE.match(nxt):
                break
            if _BLOCK_RE.match(nxt) and _indent(nxt) <= base_indent:
                break
            prop = _PROPERTY_RE.match(nxt)
            if prop:
                key, value = prop.group(1).lower(), prop.group(2).strip()
                if key == "formatstring":
                    measure.format_string = value.strip("'\"")
                elif key == "displayfolder":
                    measure.display_folder = value.strip("'\"")
                elif key == "description":
                    measure.description = value.strip("'\"")
                elif key == "ishidden" and value.lower() == "true":
                    measure.is_hidden = True
            elif nxt.strip():
                expr_parts.append(nxt.strip())
            i += 1
        measure.expression = "\n".join(p for p in expr_parts if p)
        measures.append(measure)
    return measures


# ── ZIP dispatch (.pbit and zipped PBIP) ────────────────────────────────────


def _safe_zip(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PowerBiError("The file is not a valid .pbit/.zip archive.") from None
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise PowerBiError("The archive has too many parts to read.")
    if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED:
        raise PowerBiError("The archive unpacks to more than the size limit allows.")
    return archive


def _decode_model_schema(raw: bytes) -> dict[str, Any]:
    # DataModelSchema is UTF-16 with a BOM; fall back to UTF-8 for hand-made files.
    for encoding in ("utf-16", "utf-8-sig"):
        try:
            obj = json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            return obj
    raise PowerBiError("The model schema inside the file could not be read.")


def read_zip(data: bytes) -> Model:
    with _safe_zip(data) as archive:
        names = archive.namelist()
        schema = next((n for n in names if n.rsplit("/", 1)[-1] == "DataModelSchema"), None)
        if schema is not None:
            model = parse_tmsl(_decode_model_schema(archive.read(schema)))
            model.source = "pbit"
            return model
        tmdl_names = [n for n in names if n.lower().endswith(".tmdl")]
        if tmdl_names:
            measures: list[Measure] = []
            tables: list[str] = []
            for name in sorted(tmdl_names):
                text = archive.read(name).decode("utf-8-sig", errors="replace")
                # In a PBIP project each table is its own file: tables/<Name>.tmdl.
                stem = name.rsplit("/", 1)[-1].removesuffix(".tmdl")
                found = parse_tmdl(text, table=stem)
                measures.extend(found)
                if found:
                    tables.append(stem)
            if not measures:
                raise PowerBiError("No measures were found in the PBIP project's TMDL.")
            return Model(source="tmdl", tables=sorted(set(tables)), measures=measures)
    raise PowerBiError(
        "The archive holds no Power BI model (no DataModelSchema and no .tmdl files). "
        "Save the report as a .pbit template, or export its PBIP project."
    )


def parse_model(filename: str, data: bytes) -> Model:
    """Read a Power BI model from an uploaded file, dispatching on its extension."""
    lower = filename.lower()
    if lower.endswith((".pbit", ".zip")):
        return read_zip(data)
    if lower.endswith((".bim", ".json")):
        return read_bim(data)
    if lower.endswith(".tmdl"):
        text = data.decode("utf-8-sig", errors="replace")
        measures = parse_tmdl(text)
        if not measures:
            raise PowerBiError("No measures were found in the .tmdl file.")
        return Model(
            source="tmdl",
            tables=sorted({m.table for m in measures if m.table}),
            measures=measures,
        )
    if lower.endswith(".pbix"):
        raise PowerBiError(
            "A .pbix file stores its model compressed and cannot be read directly. In "
            "Power BI Desktop choose File → Save As → Power BI template (.pbit) and upload that."
        )
    raise PowerBiError(f"'{filename}' is not a Power BI model (.pbit, .bim, .json or .tmdl).")
