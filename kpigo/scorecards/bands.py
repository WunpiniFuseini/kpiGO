"""Rating bands (PRD SC-4): label, threshold and step on the grade ramp.

Thresholds are on the total score's own scale, where 1.0 is every metric exactly
on target with weights summing to the configured total. The standard four ship
as defaults (Brief §5 decision 2); a client replaces them with ``band.set``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kpigo.scorecards.models import RatingBand


@dataclass(frozen=True)
class Band:
    label: str
    threshold: Decimal
    ramp_position: int
    band_id: str | None = None
    colour_hex: str | None = None


DEFAULT_BANDS: tuple[Band, ...] = (
    Band("Needs Focus", Decimal("0"), 1),
    Band("Gaining Momentum", Decimal("0.75"), 2),
    Band("On Target", Decimal("1.0"), 3),
    Band("Exemplary", Decimal("1.2"), 4),
)


def bands_for(org_id: str, product: str = "scorecards") -> list[Band]:
    """Lowest threshold first. No configured rows means the standard four."""
    rows = RatingBand.objects.filter(org_id=org_id, product=product).order_by("threshold")
    found = [
        Band(
            label=r.label,
            threshold=Decimal(r.threshold),
            ramp_position=r.ramp_position,
            band_id=str(r.band_id),
            colour_hex=r.colour_hex,
        )
        for r in rows
    ]
    return found or list(DEFAULT_BANDS)


def lookup(bands: list[Band], value: Decimal | None) -> Band | None:
    """The highest band whose threshold the value reaches. Below every band: the lowest."""
    if value is None or not bands:
        return None
    chosen = bands[0]
    for band in bands:
        if value >= band.threshold:
            chosen = band
    return chosen


def next_band(bands: list[Band], current: Band | None) -> Band | None:
    if current is None:
        return None
    higher = [b for b in bands if b.threshold > current.threshold]
    return higher[0] if higher else None
