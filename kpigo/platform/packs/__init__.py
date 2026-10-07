"""Starter packs (PRD OP-9, Starter Packs doc): versioned, industry-specific
configuration bundles that turn onboarding from author-from-blank into adopt-and-amend.

A pack is **configuration, never data** — it ships metric definitions, a taxonomy, rating
bands, product lines, widgets and feed-template hints, but no actuals and no target values.
Loading one is **additive and non-destructive** (doc §1.2, §8): it adds what is missing and
reports what it left alone, never overwriting a client's own work, and every metric it
creates is the client's own registry entry with no live link back to the pack.

The pack definitions live in ``catalog.py`` as plain data; ``loader.apply_pack`` turns a pack
into registry entries. Both are imported by the ``pack.list`` / ``pack.load`` actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackMetric:
    """One metric a pack defines. ``weight``, ``cap`` and ``objective`` are the taxonomy
    and profile defaults the client adopts; the rest is a registry definition."""

    code: str
    name: str
    direction: str  # higher_is_better | lower_is_better
    aggregation: str  # sum | average | latest | count | ratio
    unit: str  # currency | count | percent | days | hours | score
    products: tuple[str, ...]
    objective: str = ""  # taxonomy node label (carried; relabel-able by the client)
    target_scope: str = "profile"  # profile | subject
    collection: str = "feed"  # feed | manual_input
    is_percentage: bool = False
    decimal_places: int = 0
    weight: int | None = None
    cap: int | None = None


@dataclass(frozen=True)
class PackBand:
    label: str
    threshold: str  # decimal string; the lowest band must start at "0"
    ramp_position: int


@dataclass(frozen=True)
class Pack:
    key: str
    version: str
    title: str
    role: str
    summary: str
    metrics: tuple[PackMetric, ...]
    objectives: tuple[str, ...] = ()  # taxonomy nodes (carried)
    bands: tuple[PackBand, ...] = ()  # scorecard rating bands (applied when the org has none)
    product_lines: tuple[str, ...] = ()  # carried, activated once a feed carries the data
    widgets: tuple[str, ...] = ()  # carried widget placements (descriptive)
    campaign_defaults: dict[str, object] = field(default_factory=dict)  # carried
    feed_template: str = "tmpl_actual_monthly"


from kpigo.platform.packs.catalog import ALL_PACKS  # noqa: E402

__all__ = ["ALL_PACKS", "Pack", "PackBand", "PackMetric"]
