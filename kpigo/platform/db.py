"""Shared schema building blocks (Schema conventions).

Every table carries ``created_at`` / ``created_by``; mutable tables also
``updated_at`` / ``updated_by``. Enumerations are ``text`` + ``CHECK``.
Effective-dated rows are half-open: ``effective_from`` is the first day a row
applies and ``effective_to`` (null = open) the first day it no longer does, so
``daterange(effective_from, effective_to)`` with Postgres's default ``[)``
bounds is exactly the period a row is in force, and a row that ends on the day
the next begins does not overlap it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Now

from kpigo.action.errors import Conflict


class Stamped(models.Model):
    created_at = models.DateTimeField(db_default=Now())
    created_by = models.BigIntegerField(null=True)

    class Meta:
        abstract = True


class Tracked(Stamped):
    updated_at = models.DateTimeField(db_default=Now())
    updated_by = models.BigIntegerField(null=True)

    class Meta:
        abstract = True


class CITextField(models.TextField):  # type: ignore[type-arg]
    """Postgres ``citext``: case-insensitive equality and uniqueness (needs the extension)."""

    def db_type(self, connection: Any) -> str:
        return "citext"


class DateRange(models.Func):
    """``daterange(effective_from, effective_to)``: half-open, null upper bound = open."""

    function = "DATERANGE"
    output_field = DateRangeField()


def one_of(field: str, values: Iterable[str], name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=models.Q(**{f"{field}__in": list(values)}), name=name)


def valid_range(name: str) -> models.CheckConstraint:
    """An effective period must not be empty: ``effective_to`` is after ``effective_from``."""
    return models.CheckConstraint(
        condition=models.Q(effective_to__isnull=True)
        | models.Q(effective_to__gt=models.F("effective_from")),
        name=name,
    )


def no_overlap(name: str, *equal: str, condition: models.Q | None = None) -> ExclusionConstraint:
    """The database refuses two rows with equal ``equal`` columns whose periods overlap."""
    expressions: list[tuple[Any, str]] = [(f, RangeOperators.EQUAL) for f in equal]
    expressions.append((DateRange("effective_from", "effective_to"), RangeOperators.OVERLAPS))
    return ExclusionConstraint(
        name=name, expressions=expressions, index_type="gist", condition=condition
    )


def constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.__cause__, "diag", None)
    name = getattr(diag, "constraint_name", None)
    return str(name) if name else None


@contextmanager
def conflicts(messages: Mapping[str, str]) -> Iterator[None]:
    """Turn a named constraint violation into a ``Conflict`` the caller can act on.

    Runs in a savepoint so the surrounding transaction stays usable. A violation
    of a constraint not named here is re-raised unchanged.
    """
    try:
        with transaction.atomic():
            yield
    except IntegrityError as exc:
        name = constraint_name(exc)
        if name is not None and name in messages:
            raise Conflict(messages[name], detail={"constraint": name}) from None
        raise
