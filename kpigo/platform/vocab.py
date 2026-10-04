"""Vocabularies shared across apps. Each is a ``text`` + ``CHECK`` column in the schema."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Annotated, Literal, get_args

from pydantic import StringConstraints

Product = Literal["scorecards", "agent_sales", "agent_service", "executive", "campaign"]
PRODUCTS: tuple[str, ...] = get_args(Product)

# Manual input is only valid for Scorecards and Executive bindings (PRD MR-9).
MANUAL_INPUT_PRODUCTS = frozenset({"scorecards", "executive"})

PERIOD_KEY_PATTERN = r"^[0-9]{4}(0[1-9]|1[0-2])$"
PeriodKey = Annotated[str, StringConstraints(pattern=PERIOD_KEY_PATTERN)]
CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$"
Code = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=CODE_PATTERN)]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]

_PERIOD_KEY_RE = re.compile(PERIOD_KEY_PATTERN)


def period_key_for(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}"


def month_bounds(period_key: str) -> tuple[date, date]:
    """First day of the month and the first day of the next (half-open)."""
    if not _PERIOD_KEY_RE.match(period_key):
        raise ValueError(f"'{period_key}' is not a YYYYMM period key.")
    first = date(int(period_key[:4]), int(period_key[4:]), 1)
    following = (first + timedelta(days=32)).replace(day=1)
    return first, following
