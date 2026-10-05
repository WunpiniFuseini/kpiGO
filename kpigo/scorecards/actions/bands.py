"""Rating bands (PRD SC-4) and scoring settings (SC-7, SC-10)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Literal

from django.utils import timezone
from pydantic import BaseModel, Field, StringConstraints, model_validator

from kpigo.action import ActionContext, InvalidInput, action
from kpigo.scorecards.bands import bands_for
from kpigo.scorecards.config import settings_for
from kpigo.scorecards.models import RatingBand, ScorecardSettings

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]


class BandOut(BaseModel):
    band_id: uuid.UUID | None
    label: str
    threshold: Decimal
    ramp_position: int
    colour_hex: str | None


class BandListIn(BaseModel):
    pass


class BandListOut(BaseModel):
    # True while the org uses the standard four, not bands of its own.
    is_default: bool
    bands: list[BandOut]


def _band_list(org_id: str) -> BandListOut:
    bands = bands_for(org_id)
    return BandListOut(
        is_default=all(b.band_id is None for b in bands),
        bands=[
            BandOut(
                band_id=uuid.UUID(b.band_id) if b.band_id else None,
                label=b.label,
                threshold=b.threshold,
                ramp_position=b.ramp_position,
                colour_hex=b.colour_hex,
            )
            for b in bands
        ],
    )


@action(
    name="band.list",
    summary="The Scorecards rating bands, lowest first.",
    schema=BandListIn,
    output=BandListOut,
    permission="scorecard.view",
    read_only=True,
    module="scorecards",
    example={},
)
def list_bands(params: BandListIn, ctx: ActionContext) -> BandListOut:
    return _band_list(ctx.org_id)


class BandIn(BaseModel):
    label: Label
    # The total score the band starts at; 1.0 is every metric exactly on target.
    threshold: Decimal = Field(ge=0, le=100, max_digits=8, decimal_places=4)
    # Step on the grade ramp, 1 (lowest) to 7. Optional colour for client theming.
    ramp_position: int = Field(ge=1, le=7)
    colour_hex: Hex | None = None


class BandSetIn(BaseModel):
    # The full set, replacing the current one. Empty restores the standard four.
    bands: list[BandIn] = Field(max_length=7)

    @model_validator(mode="after")
    def _ordinal(self) -> BandSetIn:
        if not self.bands:
            return self
        ordered = sorted(self.bands, key=lambda b: b.threshold)
        if ordered[0].threshold != 0:
            raise ValueError("the lowest band must start at 0 so every score has a band")
        if len({b.threshold for b in self.bands}) != len(self.bands):
            raise ValueError("thresholds must be distinct")
        if len({b.label.lower() for b in self.bands}) != len(self.bands):
            raise ValueError("labels must be distinct")
        positions = [b.ramp_position for b in ordered]
        if positions != sorted(set(positions)):
            raise ValueError(
                "ramp positions must rise with the threshold: colour stays ordinal and monotonic"
            )
        return self


@action(
    name="band.set",
    summary="Replace the Scorecards rating bands: label, threshold and ramp step.",
    schema=BandSetIn,
    output=BandListOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="band.set",
    config_change=True,
    example={
        "bands": [
            {"label": "Needs Focus", "threshold": "0", "ramp_position": 1},
            {"label": "On Target", "threshold": "1.0", "ramp_position": 3},
        ]
    },
)
def set_bands(params: BandSetIn, ctx: ActionContext) -> BandListOut:
    RatingBand.objects.filter(org_id=ctx.org_id, product="scorecards").delete()
    for order, band in enumerate(sorted(params.bands, key=lambda b: b.threshold)):
        RatingBand.objects.create(
            org_id=ctx.org_id,
            product="scorecards",
            label=band.label,
            threshold=band.threshold,
            ramp_position=band.ramp_position,
            colour_hex=band.colour_hex,
            sort_order=order,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )
    return _band_list(ctx.org_id)


# ── settings ────────────────────────────────────────────────────────────────


class ScorecardSettingsOut(BaseModel):
    weight_total: Decimal
    weight_tolerance: Decimal
    cap_min_ratio: Decimal
    cap_max_ratio: Decimal
    denominator_policy: str


def _settings_out(org_id: str) -> ScorecardSettingsOut:
    s = settings_for(org_id)
    return ScorecardSettingsOut(
        weight_total=s.weight_total,
        weight_tolerance=s.weight_tolerance,
        cap_min_ratio=s.cap_min_ratio,
        cap_max_ratio=s.cap_max_ratio,
        denominator_policy=s.denominator_policy,
    )


class ScorecardSettingsGetIn(BaseModel):
    pass


@action(
    name="scorecard.settings.get",
    summary="Weight total and tolerance, cap plausibility and the denominator policy.",
    schema=ScorecardSettingsGetIn,
    output=ScorecardSettingsOut,
    permission="scorecard.config.view",
    read_only=True,
    module="scorecards",
    example={},
)
def get_settings(params: ScorecardSettingsGetIn, ctx: ActionContext) -> ScorecardSettingsOut:
    return _settings_out(ctx.org_id)


class ScorecardSettingsSetIn(BaseModel):
    weight_total: Decimal | None = Field(default=None, gt=0, le=1000)
    weight_tolerance: Decimal | None = Field(default=None, ge=0, le=100)
    cap_min_ratio: Decimal | None = Field(default=None, gt=0, le=10)
    cap_max_ratio: Decimal | None = Field(default=None, gt=0, le=10)
    # ``redistribute`` spreads an unscored metric's weight over the scored ones,
    # silently changing everyone's weighting; ``reduced`` (the default) does not.
    denominator_policy: Literal["reduced", "redistribute"] | None = None


@action(
    name="scorecard.settings.set",
    summary="Change the scoring settings.",
    schema=ScorecardSettingsSetIn,
    output=ScorecardSettingsOut,
    permission="scorecard.config.manage",
    read_only=False,
    module="scorecards",
    requires_approval="config_change",
    audit="scorecard.settings_set",
    config_change=True,
    example={"weight_tolerance": "0.5"},
)
def set_settings(params: ScorecardSettingsSetIn, ctx: ActionContext) -> ScorecardSettingsOut:
    changes = params.model_dump(exclude_none=True)
    row, _ = ScorecardSettings.objects.get_or_create(
        org_id=ctx.org_id, defaults={"created_by": ctx.user_id}
    )
    for key, value in changes.items():
        setattr(row, key, value)
    if Decimal(row.cap_max_ratio) < Decimal(row.cap_min_ratio):
        raise InvalidInput("cap_max_ratio must be at least cap_min_ratio.")
    row.updated_by = ctx.user_id
    row.updated_at = timezone.now()
    row.save()
    return _settings_out(ctx.org_id)
