"""Org configuration: the config_version counter, reporting timezone and FX conversion."""

from __future__ import annotations

from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import F
from django.utils import timezone

from kpigo.platform.models import FxRate, OrgSettings


def org_settings(org_id: str) -> OrgSettings:
    found, _ = OrgSettings.objects.get_or_create(org_id=org_id)
    return found


def bump_config_version(org_id: str) -> int:
    """Increment and return the org's config_version. Called by the pipeline only."""
    OrgSettings.objects.get_or_create(org_id=org_id)
    OrgSettings.objects.filter(org_id=org_id).update(
        config_version=F("config_version") + 1, updated_at=timezone.now()
    )
    return int(OrgSettings.objects.values_list("config_version", flat=True).get(org_id=org_id))


def config_version(org_id: str) -> int:
    found = OrgSettings.objects.filter(org_id=org_id).values_list("config_version", flat=True)
    return int(found.first() or 0)


def reporting_zone(org_id: str) -> ZoneInfo:
    name = (
        OrgSettings.objects.filter(org_id=org_id)
        .values_list("reporting_timezone", flat=True)
        .first()
    )
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


class MissingRate(LookupError):
    pass


def fx_rate(
    org_id: str, from_currency: str, to_currency: str, period_key: str, rate_type: str = "average"
) -> Decimal:
    """The rate for one unit of ``from_currency`` in ``to_currency``.

    A direct rate wins; otherwise the inverse of the opposite pair is used. A
    missing rate is an error, never an assumed 1: an unconverted figure would
    silently misstate a roll-up.
    """
    if from_currency == to_currency:
        return Decimal(1)
    rates = FxRate.objects.filter(org_id=org_id, period_key=period_key, rate_type=rate_type)
    direct = rates.filter(from_currency=from_currency, to_currency=to_currency).first()
    if direct is not None:
        return Decimal(direct.rate)
    inverse = rates.filter(from_currency=to_currency, to_currency=from_currency).first()
    if inverse is not None:
        return Decimal(1) / Decimal(inverse.rate)
    raise MissingRate(
        f"No {rate_type} rate {from_currency}->{to_currency} for period {period_key}."
    )


def convert(
    org_id: str,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    period_key: str,
    rate_type: str = "average",
) -> Decimal:
    return amount * fx_rate(org_id, from_currency, to_currency, period_key, rate_type)
