"""Render a scorecard to PDF (PRD SC-17).

Takes the same ``ScorecardOut`` the screen shows and lays it out on one A4
landscape page: who, which period and on what footing (provisional, closed,
restated), the band in words as well as colour, the matrix, and the statement.
Nothing here computes a score.
"""

from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

if TYPE_CHECKING:
    from kpigo.scorecards.actions.scores import MetricScoreOut, ScorecardOut

STATE_WORDS = {
    "scored": "Scored",
    "zero_actual": "Scored (zero)",
    "not_reported": "Awaiting data",
    "no_target": "No target",
    "no_fx_rate": "No FX rate",
    "excluded": "Excluded",
}


def _num(value: Decimal | None, places: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{places}f}"


def _pct(value: Decimal | None) -> str:
    return "—" if value is None else f"{value * 100:,.1f}%"


def footing(card: ScorecardOut) -> str:
    if card.source == "snapshot":
        if card.restated_at is not None:
            return f"Restated (version {card.snapshot_version}): {card.restatement_reason}"
        return f"Closed and published (version {card.snapshot_version})"
    if card.period_status == "restating":
        return "Being restated: figures are provisional until the period closes again"
    return "Provisional: figures can change until the period closes"


def _row(m: MetricScoreOut) -> list[str]:
    return [
        " › ".join([*m.path, m.display_name]),
        _num(m.target_value, m.decimal_places),
        _num(m.actual_value, m.decimal_places),
        _pct(m.pct_achieved),
        _num(m.weight, 0),
        _num(None if m.score is None else m.score * 100, 2),
        _num(m.cap, 0),
        STATE_WORDS.get(m.state, m.state),
    ]


def render(card: ScorecardOut, generated_at: datetime) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"Scorecard {card.staff_no} {card.period_key}",
        author="kpiGo",
    )
    styles = getSampleStyleSheet()
    story: list[object] = [
        Paragraph(f"{card.full_name} · {card.staff_no}", styles["Title"]),
        Paragraph(
            f"Scorecard for {card.period_key[:4]}-{card.period_key[4:]}"
            + (f" · profile {card.profile_code}" if card.profile_code else ""),
            styles["Heading3"],
        ),
        Paragraph(footing(card), styles["Normal"]),
        Spacer(1, 4 * mm),
    ]
    if not card.assigned:
        story.append(Paragraph(card.statement, styles["Normal"]))
    else:
        band = card.band.label if card.band else "No band"
        points = _num(None if card.graded_score is None else card.graded_score * 100, 1)
        story += [
            Paragraph(f"<b>{band}</b> · {points} points", styles["Heading2"]),
            Paragraph(
                f"Total {_num(card.total_score * 100, 2)} of {_num(card.total_cap, 0)} · "
                f"achievement {_pct(card.achievement_pct)} · {card.statement}",
                styles["Normal"],
            ),
            Spacer(1, 4 * mm),
        ]
        header = ["Metric", "Target", "Actual", "% achieved", "Weight", "Score", "Cap", "Status"]
        table = Table(
            [header, *(_row(m) for m in card.metrics)],
            colWidths=[95 * mm, 26 * mm, 26 * mm, 24 * mm, 18 * mm, 18 * mm, 16 * mm, 30 * mm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("ALIGN", (1, 0), (-2, -1), "RIGHT"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
        excluded = [m for m in card.metrics if m.state == "excluded" and m.exclusion_reason]
        if excluded:
            story.append(Spacer(1, 3 * mm))
            for m in excluded:
                story.append(
                    Paragraph(f"{m.display_name} excluded: {m.exclusion_reason}", styles["Normal"])
                )
    story += [
        Spacer(1, 6 * mm),
        Paragraph(
            f"Generated {generated_at:%Y-%m-%d %H:%M} UTC by kpiGo. Every figure traces to its "
            "target version, approved overrides and the feed run that loaded it.",
            styles["Italic"],
        ),
    ]
    doc.build(story)
    return buffer.getvalue()
