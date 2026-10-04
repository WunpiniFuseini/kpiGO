"""Org settings, currencies and FX rates (PRD AD-11; Schema §4)."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import available_timezones

from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from kpigo.action import ActionContext, InvalidInput, action
from kpigo.platform.config import org_settings
from kpigo.platform.models import Currency, FxRate
from kpigo.platform.vocab import CurrencyCode, PeriodKey

RateType = Literal["average", "closing", "budget"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SettingsOut(BaseModel):
    reporting_currency: str | None
    reporting_timezone: str
    business_day_cutoff: time | None
    config_version: int


def _settings_out(org_id: str) -> SettingsOut:
    found = org_settings(org_id)
    return SettingsOut(
        reporting_currency=found.reporting_currency,
        reporting_timezone=found.reporting_timezone,
        business_day_cutoff=found.business_day_cutoff,
        config_version=found.config_version,
    )


class SettingsGetIn(BaseModel):
    pass


@action(
    name="settings.get",
    summary="Reporting currency, timezone, business-day cutoff and the config version.",
    schema=SettingsGetIn,
    output=SettingsOut,
    permission="settings.view",
    read_only=True,
    example={},
)
def get_settings(params: SettingsGetIn, ctx: ActionContext) -> SettingsOut:
    return _settings_out(ctx.org_id)


class SettingsUpdateIn(BaseModel):
    reporting_currency: CurrencyCode | None = None
    reporting_timezone: str | None = Field(default=None, max_length=64)
    business_day_cutoff: time | None = None
    # Set to remove the cutoff, so the business day ends at midnight.
    clear_cutoff: bool = False

    @model_validator(mode="after")
    def _cutoff(self) -> SettingsUpdateIn:
        if self.clear_cutoff and self.business_day_cutoff is not None:
            raise ValueError("pass business_day_cutoff or clear_cutoff, not both")
        if self.business_day_cutoff is not None and self.business_day_cutoff.tzinfo is not None:
            raise ValueError("business_day_cutoff is a local time in the reporting timezone")
        return self


@action(
    name="settings.update",
    summary="Change the reporting currency, timezone or business-day cutoff.",
    schema=SettingsUpdateIn,
    output=SettingsOut,
    permission="settings.manage",
    read_only=False,
    requires_approval="config_change",
    audit="settings.updated",
    config_change=True,
    example={
        "reporting_currency": "GHS",
        "reporting_timezone": "Africa/Accra",
        "business_day_cutoff": "17:00:00",
    },
)
def update_settings(params: SettingsUpdateIn, ctx: ActionContext) -> SettingsOut:
    found = org_settings(ctx.org_id)
    changes = params.model_dump(exclude={"clear_cutoff"}, exclude_none=True)
    if not changes and not params.clear_cutoff:
        raise InvalidInput("Nothing to change.")
    if (
        params.reporting_currency is not None
        and not Currency.objects.filter(
            org_id=ctx.org_id, code=params.reporting_currency, is_active=True
        ).exists()
    ):
        raise InvalidInput(f"'{params.reporting_currency}' is not an active currency.")
    if (
        params.reporting_timezone is not None
        and params.reporting_timezone not in available_timezones()
    ):
        raise InvalidInput(f"'{params.reporting_timezone}' is not a known IANA timezone.")
    for field, value in changes.items():
        setattr(found, field, value)
    if params.clear_cutoff:
        found.business_day_cutoff = None
    found.updated_by = ctx.user_id
    found.updated_at = timezone.now()
    found.save(
        update_fields=[
            "reporting_currency",
            "reporting_timezone",
            "business_day_cutoff",
            "updated_by",
            "updated_at",
        ]
    )
    return _settings_out(ctx.org_id)


# ── currency ────────────────────────────────────────────────────────────────


class CurrencyIn(BaseModel):
    code: CurrencyCode
    name: Name
    minor_units: int = Field(default=2, ge=0, le=4)
    is_active: bool = True


class CurrencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    minor_units: int
    is_active: bool


@action(
    name="currency.upsert",
    summary="Add a currency or update its name, minor units or active flag.",
    schema=CurrencyIn,
    output=CurrencyOut,
    permission="settings.manage",
    read_only=False,
    requires_approval="config_change",
    audit="currency.upserted",
    config_change=True,
    example={"code": "GHS", "name": "Ghanaian cedi"},
)
def upsert_currency(params: CurrencyIn, ctx: ActionContext) -> CurrencyOut:
    changed = Currency.objects.filter(org_id=ctx.org_id, code=params.code).update(
        name=params.name, minor_units=params.minor_units, is_active=params.is_active
    )
    if not changed:
        Currency.objects.create(org_id=ctx.org_id, **params.model_dump(), created_by=ctx.user_id)
    return CurrencyOut(**params.model_dump())


class CurrencyListIn(BaseModel):
    active_only: bool = False


class CurrencyListOut(BaseModel):
    currencies: list[CurrencyOut]


@action(
    name="currency.list",
    summary="Currencies configured for the org.",
    schema=CurrencyListIn,
    output=CurrencyListOut,
    permission="settings.view",
    read_only=True,
    example={},
)
def list_currencies(params: CurrencyListIn, ctx: ActionContext) -> CurrencyListOut:
    rows = Currency.objects.filter(org_id=ctx.org_id)
    if params.active_only:
        rows = rows.filter(is_active=True)
    return CurrencyListOut(
        currencies=[CurrencyOut.model_validate(c) for c in rows.order_by("code")]
    )


# ── fx_rate ─────────────────────────────────────────────────────────────────


class RateIn(BaseModel):
    from_currency: CurrencyCode
    to_currency: CurrencyCode
    period_key: PeriodKey
    rate_type: RateType = "average"
    rate: Decimal = Field(gt=0, max_digits=24, decimal_places=10)

    @model_validator(mode="after")
    def _distinct(self) -> RateIn:
        if self.from_currency == self.to_currency:
            raise ValueError("from_currency and to_currency must differ")
        return self


class RateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_currency: str
    to_currency: str
    period_key: str
    rate_type: str
    rate: Decimal


class RatesSetIn(BaseModel):
    rates: list[RateIn] = Field(min_length=1, max_length=5000)


class RatesSetOut(BaseModel):
    created: int
    updated: int


@action(
    name="fx.set",
    summary="Load FX rates for a period. One unit of from_currency = rate to_currency.",
    schema=RatesSetIn,
    output=RatesSetOut,
    permission="fx.manage",
    read_only=False,
    requires_approval="config_change",
    audit="fx.rates_set",
    config_change=True,
    example={
        "rates": [
            {"from_currency": "USD", "to_currency": "GHS", "period_key": "202610", "rate": "15.25"}
        ]
    },
)
def set_rates(params: RatesSetIn, ctx: ActionContext) -> RatesSetOut:
    codes = {r.from_currency for r in params.rates} | {r.to_currency for r in params.rates}
    known = set(
        Currency.objects.filter(org_id=ctx.org_id, code__in=codes).values_list("code", flat=True)
    )
    if codes - known:
        raise InvalidInput("Unknown currencies.", detail={"unknown": sorted(codes - known)})
    keys = [(r.from_currency, r.to_currency, r.period_key, r.rate_type) for r in params.rates]
    if len(set(keys)) != len(keys):
        raise InvalidInput("Each currency pair, period and rate type may appear once.")
    created = updated = 0
    now = timezone.now()
    for r in params.rates:
        changed = FxRate.objects.filter(
            org_id=ctx.org_id,
            from_currency=r.from_currency,
            to_currency=r.to_currency,
            period_key=r.period_key,
            rate_type=r.rate_type,
        ).update(rate=r.rate, updated_by=ctx.user_id, updated_at=now)
        if changed:
            updated += 1
            continue
        FxRate.objects.create(
            org_id=ctx.org_id, **r.model_dump(), created_by=ctx.user_id, updated_by=ctx.user_id
        )
        created += 1
    return RatesSetOut(created=created, updated=updated)


class RatesListIn(BaseModel):
    period_key: PeriodKey
    rate_type: RateType | None = None


class RatesListOut(BaseModel):
    rates: list[RateOut]


@action(
    name="fx.list",
    summary="FX rates loaded for a period.",
    schema=RatesListIn,
    output=RatesListOut,
    permission="settings.view",
    read_only=True,
    example={"period_key": "202610"},
)
def list_rates(params: RatesListIn, ctx: ActionContext) -> RatesListOut:
    rows = FxRate.objects.filter(org_id=ctx.org_id, period_key=params.period_key)
    if params.rate_type is not None:
        rows = rows.filter(rate_type=params.rate_type)
    return RatesListOut(
        rates=[
            RateOut.model_validate(r)
            for r in rows.order_by("rate_type", "from_currency", "to_currency")
        ]
    )
