"""Sales and Service presets (PRD AP-4; Scope §8.2; Starter Packs §4.2).

The two modules share one engine and differ in metric bindings and in the page
they open on. A preset is that page: which sections show, in what order, and
which metric each starts on. Sections only ever read the module's own metrics:
the starter-pack codes (``so_tat`` for SO_TAT) are preferred when the client kept them, and otherwise a
section picks the first metric that suits it.

Sales opens on the leaderboard, the product mix (the product-line matrix) and
month to date against target. Service opens on the SLA heatmap, the TAT
distribution, the queue trend and a leaderboard ranked by resolution with TAT
as the tie-break.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from kpigo.agents.pace import ADDITIVE
from kpigo.metrics.models import Metric

Kind = Literal["leaderboard", "matrix", "trend", "heatmap", "distribution"]


def _additive(m: Metric) -> bool:
    return m.aggregation in ADDITIVE


def _lower(m: Metric) -> bool:
    return m.direction == "lower_is_better"


def _lower_average(m: Metric) -> bool:
    return _lower(m) and not _additive(m)


def _any(m: Metric) -> bool:
    return True


@dataclass(frozen=True)
class Section:
    key: str
    kind: Kind
    title: str
    caption: str
    # Starter-pack codes this section starts on when the module carries them.
    prefer: tuple[str, ...] = ()
    # Otherwise the first metric (by name) this accepts.
    suits: Callable[[Metric], bool] = _any
    # Which metrics the section can switch to.
    accepts: Callable[[Metric], bool] = _any

    def pick(self, metrics: Iterable[Metric]) -> Metric | None:
        ordered = sorted(metrics, key=lambda m: (m.display_name.lower(), m.metric_code))
        allowed = [m for m in ordered if self.accepts(m)]
        by_code = {m.metric_code: m for m in allowed}
        for code in self.prefer:
            if code in by_code:
                return by_code[code]
        return next((m for m in allowed if self.suits(m)), allowed[0] if allowed else None)

    def options(self, metrics: Iterable[Metric]) -> list[Metric]:
        return sorted(
            (m for m in metrics if self.accepts(m)),
            key=lambda m: (m.display_name.lower(), m.metric_code),
        )


@dataclass(frozen=True)
class Preset:
    product: str
    sections: tuple[Section, ...]
    # The leaderboard's ranking until an Admin saves the module's own settings.
    rank_metric_code: str | None = None
    tiebreak_metric_code: str | None = None


LEADERBOARD = Section(
    "leaderboard",
    "leaderboard",
    "Leaderboard",
    "Ranked within a peer group, to date.",
)

PRESETS: dict[str, Preset] = {
    "agent_sales": Preset(
        product="agent_sales",
        sections=(
            LEADERBOARD,
            Section(
                "product_mix",
                "matrix",
                "Product mix",
                "Actual against the target expected by now, per product line.",
                accepts=_additive,
            ),
            Section(
                "to_date",
                "trend",
                "Month to date",
                "What has been booked so far against where the target expects it.",
                prefer=("rm_ntb_accounts", "rm_fee_income"),
                suits=_additive,
            ),
        ),
    ),
    "agent_service": Preset(
        product="agent_service",
        sections=(
            Section(
                "sla",
                "heatmap",
                "SLA by day",
                "Each day against its target, by branch.",
                prefer=("so_sla_breach",),
                suits=_lower_average,
            ),
            Section(
                "tat",
                "distribution",
                "TAT distribution",
                "How agents spread on handling time, to date.",
                prefer=("so_tat",),
                suits=_lower_average,
            ),
            Section(
                "queue",
                "trend",
                "Queue trend",
                "Work handled each day.",
                prefer=("so_complaints",),
                suits=_additive,
            ),
            LEADERBOARD,
        ),
        rank_metric_code="so_resolution",
        tiebreak_metric_code="so_tat",
    ),
}


def preset_for(product: str) -> Preset:
    return PRESETS[product]
