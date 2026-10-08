"""Turn a client's own KPI spreadsheet into proposed kpiGo config (R6, migration assistant).

A bank migrating off Excel arrives with a scorecard-definition sheet (one row per
metric: a name, a weight, a target, sometimes a direction and a unit) or a staff
roster (staff number, name, email). This module reads such a sheet — already parsed
to a header and text rows by :mod:`kpigo.ingestion.validator` — and proposes the
metrics, targets/weights and subjects kpiGo would hold, together with the column
mapping it inferred, so an Admin can review before anything is written.

It is pure: it imports nothing from kpiGo or Django and keeps its own code-slug
generator, so it is cheap to test and carries no layering weight. Every inference is
a guess the reviewer corrects; each proposal records whether a field was read from the
sheet or inferred, and nothing here writes. Applying a reviewed draft is the job of
``import.draft.apply``, which calls the existing ``metric.register`` and
``subject.register`` actions.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

Kind = Literal["scorecard", "roster", "unknown"]

# The most metrics or subjects one sheet may propose; a larger sheet is read up to the
# validator's row cap and the overflow is reported, never silently dropped.
MAX_METRICS = 2000
MAX_SUBJECTS = 20000


# ── header matching ───────────────────────────────────────────────────────────


def _norm(text: str | None) -> str:
    """A header or cell as a comparison key: lowercase, non-alphanumerics to spaces."""
    return re.sub(r"[^a-z0-9%#]+", " ", (text or "").lower()).strip()


# A role (the kpiGo concept) and the header phrases that name it, most specific first.
# A header matches a role when its normalised form equals one of the phrases.
_SCORECARD_ROLES: dict[str, tuple[str, ...]] = {
    "metric_name": (
        "metric name",
        "kpi name",
        "measure name",
        "metric",
        "kpi",
        "measure",
        "indicator",
        "name",
    ),
    "weight": ("weight", "weighting", "weight %", "weight pct", "wt"),
    "target": ("target", "target value", "goal", "budget", "objective"),
    "direction": ("direction", "polarity", "higher is better", "trend", "good direction"),
    "unit": ("unit", "uom", "unit of measure", "measure unit"),
    "description": ("description", "definition", "formula", "calculation", "notes", "note"),
}
_ROSTER_ROLES: dict[str, tuple[str, ...]] = {
    "staff_no": (
        "staff no",
        "staff number",
        "staff id",
        "employee id",
        "employee no",
        "emp id",
        "staff",
        "id",
    ),
    "full_name": ("full name", "employee name", "staff name", "name"),
    "email": ("email", "e mail", "email address"),
}


def _map_columns(header: list[str], roles: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Map each role to the first header column that names it, each column used once."""
    normalised = [_norm(h) for h in header]
    taken: set[int] = set()
    mapping: dict[str, str] = {}
    for role, phrases in roles.items():
        for phrase in phrases:
            match = next(
                (i for i, h in enumerate(normalised) if h == phrase and i not in taken), None
            )
            if match is not None:
                mapping[role] = header[match]
                taken.add(match)
                break
    return mapping


def detect_kind(header: list[str]) -> tuple[Kind, dict[str, str]]:
    """Which sheet this is, and the column mapping for it.

    A scorecard sheet names a metric and at least one of weight/target/direction; a
    roster names a staff number and a full name. When both fit, the fuller mapping wins.
    """
    scorecard = _map_columns(header, _SCORECARD_ROLES)
    roster = _map_columns(header, _ROSTER_ROLES)
    is_scorecard = "metric_name" in scorecard and bool(
        {"weight", "target", "direction"} & set(scorecard)
    )
    is_roster = "staff_no" in roster and "full_name" in roster
    if is_scorecard and is_roster:
        return ("scorecard", scorecard) if len(scorecard) >= len(roster) else ("roster", roster)
    if is_scorecard:
        return "scorecard", scorecard
    if is_roster:
        return "roster", roster
    return "unknown", {}


# ── value inference ───────────────────────────────────────────────────────────

_DIRECTION_LOWER = {
    "lower",
    "lower is better",
    "down",
    "decrease",
    "decreasing",
    "min",
    "minimise",
    "minimize",
    "less is better",
    "negative",
    "low",
}
_DIRECTION_HIGHER = {
    "higher",
    "higher is better",
    "up",
    "increase",
    "increasing",
    "max",
    "maximise",
    "maximize",
    "more is better",
    "positive",
    "high",
}
# Metric names that almost always mean "less is better".
_LOWER_HINTS = (
    "cost",
    "attrition",
    "complaint",
    "default",
    "overdue",
    "turnaround",
    "tat",
    "error",
    "breach",
    "npl",
    "par",
    "delinquen",
    "backlog",
    "downtime",
    "loss",
    "aging",
    "ageing",
    "dpd",
    "dso",
    "churn",
    "reject",
    "wait",
)
_PERCENT_HINTS = (
    "rate",
    "ratio",
    "%",
    "percentage",
    "percent",
    "margin",
    "utilis",
    "utiliz",
    "penetration",
    "share",
    "coverage",
    "compliance",
    "accuracy",
    "conversion",
    "csat",
    "cross sell",
    "adoption",
    "retention",
)
_CURRENCY_HINTS = (
    "deposit",
    "revenue",
    "income",
    "balance",
    "amount",
    "value",
    "loan",
    "sales",
    "profit",
    "aum",
    "portfolio",
    "disbursement",
    "collection",
    "repayment",
    "fee",
    "turnover",
    "assets",
    "liabilit",
)
_SCORE_HINTS = ("score", "index", "rating", "nps", "points")

# A unit column's value → a kpiGo unit.
_UNIT_WORDS: dict[str, tuple[str, ...]] = {
    "percent": ("percent", "percentage", "%", "pct", "rate", "ratio"),
    "currency": (
        "currency",
        "money",
        "amount",
        "ghs",
        "usd",
        "gbp",
        "eur",
        "cedi",
        "cedis",
        "dollar",
        "value",
    ),
    "days": ("days", "day"),
    "hours": ("hours", "hour", "hrs", "hr"),
    "count": (
        "count",
        "number",
        "no",
        "nos",
        "#",
        "volume",
        "qty",
        "quantity",
        "units",
        "customers",
        "accounts",
    ),
    "score": ("score", "index", "points", "rating"),
}


def _infer_direction(name: str, raw: str | None) -> tuple[str, bool]:
    """(direction, inferred). A direction column wins; else the name's own words."""
    value = _norm(raw)
    if value:
        if any(word in value for word in _DIRECTION_LOWER):
            return "lower_is_better", False
        if any(word in value for word in _DIRECTION_HIGHER):
            return "higher_is_better", False
    low = name.lower()
    if any(hint in low for hint in _LOWER_HINTS):
        return "lower_is_better", True
    return "higher_is_better", True


def _infer_unit(name: str, unit_raw: str | None, target_raw: str | None) -> tuple[str, bool]:
    """(unit, inferred). A unit column wins; else the name and the target's own shape."""
    value = _norm(unit_raw)
    if value:
        for unit, words in _UNIT_WORDS.items():
            if any(word == value or word in value.split() for word in words):
                return unit, False
    low = name.lower()
    target = (target_raw or "").strip()
    if "%" in low or "%" in target or any(hint in low for hint in _PERCENT_HINTS):
        return "percent", True
    if any(hint in low for hint in _SCORE_HINTS):
        return "score", True
    if any(sym in target for sym in ("$", "£", "€")) or any(
        hint in low for hint in _CURRENCY_HINTS
    ):
        return "currency", True
    return "count", True


def _aggregation_for(unit: str) -> str:
    """A reasonable default aggregation per unit; the reviewer changes it if wrong."""
    return "average" if unit in ("percent", "score") else "sum"


def _decimals_for(unit: str) -> int:
    return 0 if unit == "count" else 2


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _number(text: str | None) -> str | None:
    """A target or weight as a plain number string, stripping %, currency symbols, commas."""
    if text is None:
        return None
    cleaned = text.replace(",", "").strip()
    match = _NUMBER_RE.search(cleaned)
    return match.group(0) if match else None


def _slug(name: str, taken: set[str]) -> str:
    """A metric_code matching ^[a-z][a-z0-9_]*$, unique within this sheet."""
    base = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_")
    if not base or not base[0].isalpha():
        base = f"m_{base}".strip("_")
    base = base[:72] or "metric"
    code = base
    n = 2
    while code in taken:
        code = f"{base}_{n}"
        n += 1
    taken.add(code)
    return code


# ── proposals ───────────────────────────────────────────────────────────────


class ProposedMetric(BaseModel):
    source_row: int
    display_name: str
    metric_code: str
    direction: str
    aggregation: str
    unit: str
    is_percentage: bool
    decimal_places: int
    description: str = ""
    # Which of the above kpiGo guessed rather than read from the sheet.
    inferred: list[str]


class ProposedTarget(BaseModel):
    source_row: int
    metric_code: str
    target_value: str | None
    weight: str | None


class ProposedSubject(BaseModel):
    source_row: int
    staff_no: str
    full_name: str
    email: str


class Proposals(BaseModel):
    kind: Kind
    # role -> the sheet column it was read from.
    mapping: dict[str, str]
    unmapped_columns: list[str]
    metrics: list[ProposedMetric]
    targets: list[ProposedTarget]
    subjects: list[ProposedSubject]
    # One line per thing a reviewer should know (truncation, blank rows, duplicates).
    warnings: list[str]
    # Why kpiGo could not read the sheet, when kind is "unknown".
    message: str = ""

    def summary(self) -> dict[str, int]:
        return {
            "metrics": len(self.metrics),
            "targets": len(self.targets),
            "subjects": len(self.subjects),
            "warnings": len(self.warnings),
        }


Rows = list[tuple[str | None, ...]]


def _cells(header: list[str], row: tuple[str | None, ...]) -> dict[str, str | None]:
    return {header[i]: row[i] for i in range(min(len(header), len(row)))}


def _scorecard(header: list[str], rows: Rows, mapping: dict[str, str]) -> Proposals:
    metrics: list[ProposedMetric] = []
    targets: list[ProposedTarget] = []
    warnings: list[str] = []
    taken: set[str] = set()
    for n, row in enumerate(rows, start=1):
        cells = _cells(header, row)
        name = (cells.get(mapping["metric_name"]) or "").strip()
        if not name:
            continue  # a blank name is a spacer row, not a metric
        if len(metrics) >= MAX_METRICS:
            warnings.append(
                f"Only the first {MAX_METRICS} metrics were read; the rest were skipped."
            )
            break
        unit_raw = cells.get(mapping["unit"]) if "unit" in mapping else None
        target_raw = cells.get(mapping["target"]) if "target" in mapping else None
        dir_raw = cells.get(mapping["direction"]) if "direction" in mapping else None
        direction, dir_inferred = _infer_direction(name, dir_raw)
        unit, unit_inferred = _infer_unit(name, unit_raw, target_raw)
        inferred: list[str] = ["aggregation"]
        if dir_inferred:
            inferred.append("direction")
        if unit_inferred:
            inferred.append("unit")
        code = _slug(name, taken)
        metrics.append(
            ProposedMetric(
                source_row=n,
                display_name=name[:200],
                metric_code=code,
                direction=direction,
                aggregation=_aggregation_for(unit),
                unit=unit,
                is_percentage=unit == "percent",
                decimal_places=_decimals_for(unit),
                description=(
                    (cells.get(mapping["description"]) or "")[:4000]
                    if "description" in mapping
                    else ""
                ),
                inferred=inferred,
            )
        )
        target_value = _number(target_raw) if "target" in mapping else None
        weight = _number(cells.get(mapping["weight"])) if "weight" in mapping else None
        if "target" in mapping and target_raw and target_value is None:
            warnings.append(f"Row {n}: target '{target_raw.strip()}' is not a number; left blank.")
        if target_value is not None or weight is not None:
            targets.append(
                ProposedTarget(
                    source_row=n, metric_code=code, target_value=target_value, weight=weight
                )
            )
    if weights := [t.weight for t in targets if t.weight is not None]:
        total = sum(float(w) for w in weights)
        if abs(total - 100) > 0.5:
            warnings.append(
                f"The {len(weights)} weights add up to {total:g}, not 100. Review them."
            )
    return Proposals(
        kind="scorecard",
        mapping=mapping,
        unmapped_columns=[h for h in header if h not in set(mapping.values())],
        metrics=metrics,
        targets=targets,
        subjects=[],
        warnings=warnings,
    )


def _roster(header: list[str], rows: Rows, mapping: dict[str, str]) -> Proposals:
    subjects: list[ProposedSubject] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for n, row in enumerate(rows, start=1):
        cells = _cells(header, row)
        staff_no = (cells.get(mapping["staff_no"]) or "").strip()
        full_name = (cells.get(mapping["full_name"]) or "").strip()
        if not staff_no and not full_name:
            continue
        if not staff_no or not full_name:
            warnings.append(f"Row {n}: a staff number and a name are both needed; skipped.")
            continue
        if len(subjects) >= MAX_SUBJECTS:
            warnings.append(
                f"Only the first {MAX_SUBJECTS} people were read; the rest were skipped."
            )
            break
        if staff_no in seen:
            warnings.append(f"Row {n}: staff number '{staff_no}' repeats; only the first is kept.")
            continue
        seen.add(staff_no)
        subjects.append(
            ProposedSubject(
                source_row=n,
                staff_no=staff_no[:64],
                full_name=full_name[:200],
                email=(cells.get(mapping["email"]) or "").strip()[:254]
                if "email" in mapping
                else "",
            )
        )
    return Proposals(
        kind="roster",
        mapping=mapping,
        unmapped_columns=[h for h in header if h not in set(mapping.values())],
        metrics=[],
        targets=[],
        subjects=subjects,
        warnings=warnings,
    )


def propose(header: list[str], rows: Rows) -> Proposals:
    """Read a parsed sheet and propose the config it describes."""
    clean_header = [h for h in header if h]
    kind, mapping = detect_kind(clean_header)
    if kind == "scorecard":
        return _scorecard(clean_header, rows, mapping)
    if kind == "roster":
        return _roster(clean_header, rows, mapping)
    return Proposals(
        kind="unknown",
        mapping={},
        unmapped_columns=clean_header,
        metrics=[],
        targets=[],
        subjects=[],
        warnings=[],
        message=(
            "kpiGo could not tell what this sheet holds. For a scorecard, give it a column "
            "named Metric (or KPI) and at least one of Weight, Target or Direction. For a "
            "staff list, give it Staff No and Full Name. Columns found: "
            + (", ".join(clean_header) or "none")
            + "."
        ),
    )
