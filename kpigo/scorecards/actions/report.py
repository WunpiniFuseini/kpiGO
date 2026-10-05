"""A subject's scorecard over time, and on paper (PRD SC-8, SC-17).

``scorecard.history`` is one figure per period: the frozen total for a closed
period, the provisional live total for one still open. A period before the
subject held a role is left out, not shown as zero. ``scorecard.export.pdf``
renders exactly what ``scorecard.compute`` returns, so the PDF and the screen
cannot disagree.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from decimal import Decimal

from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, NotFound, action
from kpigo.hierarchy.models import Subject
from kpigo.hierarchy.scope import current_period_key
from kpigo.platform.vocab import PeriodKey
from kpigo.scorecards.actions.scores import scorecard_out
from kpigo.scorecards.config import period_status
from kpigo.scorecards.cycles import shift
from kpigo.scorecards.models import ScoreTotal
from kpigo.scorecards.pdf import render
from kpigo.scorecards.scoring import score_subject

EXAMPLE_ID = "00000000-0000-0000-0000-000000000000"


def _subject(ctx: ActionContext, subject_id: uuid.UUID) -> Subject:
    subject = Subject.objects.filter(org_id=ctx.org_id, subject_id=subject_id).first()
    if subject is None:
        raise NotFound("No such subject.")
    return subject


class HistoryIn(BaseModel):
    subject_id: uuid.UUID
    # The last period shown; defaults to the current one.
    to_period: PeriodKey | None = None
    periods: int = Field(default=12, ge=1, le=36)


class HistoryPointOut(BaseModel):
    period_key: str
    # live (provisional) | snapshot (frozen at close)
    source: str
    snapshot_version: int | None
    total_score: Decimal
    graded_score: Decimal | None
    achievement_pct: Decimal | None
    metrics_scored: int
    metrics_total: int
    band_label: str | None
    band_ramp_position: int | None


class HistoryOut(BaseModel):
    subject_id: uuid.UUID
    points: list[HistoryPointOut]
    # Periods in the window with no scorecard: before a role, or between roles.
    missing: list[str]


@action(
    name="scorecard.history",
    summary="A subject's total and band period by period, frozen where closed.",
    schema=HistoryIn,
    output=HistoryOut,
    permission="scorecard.view",
    read_only=True,
    module="scorecards",
    scope="subject",
    example={"subject_id": EXAMPLE_ID, "periods": 12},
)
def history(params: HistoryIn, ctx: ActionContext) -> HistoryOut:
    subject = _subject(ctx, params.subject_id)
    last = params.to_period or current_period_key(ctx.org_id)
    keys = [shift(last, -n) for n in range(params.periods - 1, -1, -1)]
    frozen = {
        t.period_key: t
        for t in ScoreTotal.objects.filter(
            org_id=ctx.org_id, subject=subject, period_key__in=keys, is_current=True
        )
    }
    points: list[HistoryPointOut] = []
    missing: list[str] = []
    for key in keys:
        if period_status(ctx.org_id, key) == "closed":
            t = frozen.get(key)
            if t is None:
                missing.append(key)
                continue
            points.append(
                HistoryPointOut(
                    period_key=key,
                    source="snapshot",
                    snapshot_version=t.snapshot_version,
                    total_score=t.total_score,
                    graded_score=t.graded_score,
                    achievement_pct=t.achievement_pct,
                    metrics_scored=t.metrics_scored,
                    metrics_total=t.metrics_total,
                    band_label=t.band_label,
                    band_ramp_position=t.band_ramp_position,
                )
            )
            continue
        s = score_subject(ctx.org_id, str(subject.subject_id), key)
        if s is None:
            missing.append(key)
            continue
        points.append(
            HistoryPointOut(
                period_key=key,
                source="live",
                snapshot_version=None,
                total_score=s.total_score,
                graded_score=s.graded_score,
                achievement_pct=s.achievement_pct,
                metrics_scored=s.metrics_scored,
                metrics_total=s.metrics_total,
                band_label=s.band.label if s.band else None,
                band_ramp_position=s.band.ramp_position if s.band else None,
            )
        )
    return HistoryOut(subject_id=subject.subject_id, points=points, missing=missing)


class ExportPdfIn(BaseModel):
    subject_id: uuid.UUID
    period_key: PeriodKey | None = None


class FileOut(BaseModel):
    filename: str
    media_type: str
    content_base64: str
    generated_at: datetime


@action(
    name="scorecard.export.pdf",
    summary="One scorecard as a PDF, exactly as it reads on screen.",
    schema=ExportPdfIn,
    output=FileOut,
    permission="scorecard.view",
    read_only=True,
    module="scorecards",
    scope="subject",
    example={"subject_id": EXAMPLE_ID, "period_key": "202609"},
)
def export_pdf(params: ExportPdfIn, ctx: ActionContext) -> FileOut:
    subject = _subject(ctx, params.subject_id)
    card = scorecard_out(ctx.org_id, subject, params.period_key or current_period_key(ctx.org_id))
    now = timezone.now()
    return FileOut(
        filename=f"scorecard-{card.staff_no}-{card.period_key}.pdf",
        media_type="application/pdf",
        content_base64=base64.b64encode(render(card, now)).decode("ascii"),
        generated_at=now,
    )
