"""Daily retention as actions (PRD AP-12; see ``kpigo.ingestion.retention``).

``agent.daily.archive`` is the scheduled job (``KPIGO_RETENTION_USER``): every
month past the hot window is rolled up to monthly and its partition archived.
``agent.daily.restore`` brings a month's daily detail back.
"""

from __future__ import annotations

from datetime import date, datetime

from django.utils import timezone
from pydantic import BaseModel, Field

from kpigo.action import ActionContext, Conflict, NotFound, action
from kpigo.ingestion import retention
from kpigo.ingestion.models import DailyArchive
from kpigo.ingestion.reference import org_today
from kpigo.platform.vocab import PeriodKey, month_bounds


class DailyArchiveOut(BaseModel):
    period_key: str
    partition_name: str
    status: str
    daily_rows: int
    rolled_up_rows: int
    mixed_currency_pairs: int
    archived_at: datetime
    restored_at: datetime | None


def _out(r: DailyArchive) -> DailyArchiveOut:
    return DailyArchiveOut(
        period_key=f"{r.month.year:04d}{r.month.month:02d}",
        partition_name=r.partition_name,
        status=r.status,
        daily_rows=r.daily_rows,
        rolled_up_rows=r.rolled_up_rows,
        mixed_currency_pairs=r.mixed_currency_pairs,
        archived_at=r.archived_at,
        restored_at=r.restored_at,
    )


class DailyArchiveRunIn(BaseModel):
    # One month past the window to (re-)archive, including one an Admin restored.
    # Unset: every month past the window that is not restored.
    period_key: PeriodKey | None = None
    # At most this many months per run, oldest first, so a first run on a large
    # install does not hold one transaction for years of history.
    limit: int = Field(default=3, ge=1, le=24)


class DailyArchiveRunOut(BaseModel):
    hot_months: int
    # The first month still kept hot (YYYYMM).
    keep_from: str
    archived: list[DailyArchiveOut]
    # Months past the window still waiting for a later run.
    remaining: list[str]


@action(
    name="agent.daily.archive",
    summary="Roll daily detail past the hot window up to monthly and move it to the archive.",
    schema=DailyArchiveRunIn,
    output=DailyArchiveRunOut,
    permission="agent.retention.manage",
    read_only=False,
    module="agent_performance",
    audit="agent.daily_archived",
    example={"limit": 3},
)
def archive_daily(params: DailyArchiveRunIn, ctx: ActionContext) -> DailyArchiveRunOut:
    today = org_today(ctx.org_id)
    due = retention.archivable(today)
    if params.period_key is not None:
        wanted = month_bounds(params.period_key)[0]
        if wanted not in due:
            raise Conflict(
                f"{params.period_key} is not past the {retention.hot_months()}-month hot "
                "window with its daily detail in place, so there is nothing to archive."
            )
        due = [wanted]
    else:
        restored = set(
            DailyArchive.objects.filter(status="restored").values_list("month", flat=True)
        )
        due = [m for m in due if m not in restored]
    done = []
    now = timezone.now()
    for month in due[: params.limit]:
        r = retention.archive(month, now=now, user_id=ctx.user_id)
        ctx.audit(
            "agent.daily_month_archived",
            month=month.isoformat(),
            daily_rows=r.daily_rows,
            rolled_up_rows=r.rolled_up_rows,
            mixed_currency_pairs=r.mixed_currency_pairs,
        )
        done.append(_out(DailyArchive.objects.get(month=month)))
    keep = retention.cutoff(today)
    return DailyArchiveRunOut(
        hot_months=retention.hot_months(),
        keep_from=f"{keep.year:04d}{keep.month:02d}",
        archived=done,
        remaining=[f"{m.year:04d}{m.month:02d}" for m in due[params.limit :]],
    )


class DailyArchiveListIn(BaseModel):
    status: str | None = None


class DailyArchiveListOut(BaseModel):
    hot_months: int
    months: list[DailyArchiveOut]


@action(
    name="agent.daily.archive.list",
    summary="Months whose daily detail is archived or was restored.",
    schema=DailyArchiveListIn,
    output=DailyArchiveListOut,
    permission="agent.retention.manage",
    read_only=True,
    module="agent_performance",
    example={},
)
def list_archive(params: DailyArchiveListIn, ctx: ActionContext) -> DailyArchiveListOut:
    rows = DailyArchive.objects.all()
    if params.status:
        rows = rows.filter(status=params.status)
    return DailyArchiveListOut(
        hot_months=retention.hot_months(), months=[_out(r) for r in rows.order_by("month")]
    )


class DailyRestoreIn(BaseModel):
    period_key: PeriodKey


@action(
    name="agent.daily.restore",
    summary="Bring an archived month's daily detail back into the daily table.",
    schema=DailyRestoreIn,
    output=DailyArchiveOut,
    permission="agent.retention.manage",
    read_only=False,
    module="agent_performance",
    audit="agent.daily_restored",
    example={"period_key": "202301"},
)
def restore_daily(params: DailyRestoreIn, ctx: ActionContext) -> DailyArchiveOut:
    first: date = month_bounds(params.period_key)[0]
    record = DailyArchive.objects.select_for_update().filter(month=first).first()
    if record is None:
        raise NotFound(f"{params.period_key} has no archived daily detail.")
    if record.status != "archived":
        raise Conflict(f"{params.period_key} is already restored.")
    retention.restore(record, now=timezone.now(), user_id=ctx.user_id)
    return _out(record)
