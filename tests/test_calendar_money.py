"""Business calendar, period status, org settings, currency and FX."""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from kpigo.action import Conflict, InvalidInput, PermissionDenied
from kpigo.periods import business
from kpigo.platform.config import MissingRate, config_version, convert, fx_rate
from kpigo.platform.models import AuditLog
from tests.conftest import ORG_ID, role_ctx, run

pytestmark = pytest.mark.django_db

ACCRA = ZoneInfo("Africa/Accra")
LONDON = ZoneInfo("Europe/London")


# ── cycles ──────────────────────────────────────────────────────────────────


def test_cycle_bindings_do_not_overlap_per_product() -> None:
    fy = run("cycle.create", name="FY2026", start_month=1, end_month=12)
    alt = run("cycle.create", name="Apr-Mar", start_month=4, end_month=3)
    with pytest.raises(Conflict, match="already exists"):
        run("cycle.create", name="FY2026", start_month=1, end_month=12)
    run(
        "cycle.bind",
        product="scorecards",
        cycle_id=str(fy.cycle_id),
        effective_from="2026-01-01",
        effective_to="2027-01-01",
    )
    with pytest.raises(Conflict, match="overlapping"):
        run(
            "cycle.bind",
            product="scorecards",
            cycle_id=str(alt.cycle_id),
            effective_from="2026-06-01",
        )
    run("cycle.bind", product="scorecards", cycle_id=str(alt.cycle_id), effective_from="2027-01-01")
    run(
        "cycle.bind", product="agent_sales", cycle_id=str(alt.cycle_id), effective_from="2026-04-01"
    )
    listed = run("cycle.list")
    assert [c.name for c in listed.cycles] == ["Apr-Mar", "FY2026"]
    assert len(listed.bindings) == 3


def test_cycle_bind_unknown_cycle() -> None:
    with pytest.raises(Exception, match="No such performance cycle"):
        run(
            "cycle.bind",
            product="scorecards",
            cycle_id=str(uuid.uuid4()),
            effective_from="2026-01-01",
        )


# ── working days and the business date ─────────────────────────────────────


def test_default_week_holidays_and_regional_overrides() -> None:
    run(
        "calendar.set_days",
        days=[
            {"date": "2026-12-25", "is_working_day": False, "holiday_name": "Christmas Day"},
            {"date": "2026-12-28", "is_working_day": False, "holiday_name": "Boxing Day (obs.)"},
            # The northern region opens on the observed Boxing Day.
            {"date": "2026-12-28", "is_working_day": True, "region_code": "NORTH"},
            # A working Saturday for quarter-end.
            {"date": "2026-12-26", "is_working_day": True, "holiday_name": None},
        ],
    )
    assert business.is_working_day(ORG_ID, date(2026, 12, 24))
    assert not business.is_working_day(ORG_ID, date(2026, 12, 25))
    assert business.is_working_day(ORG_ID, date(2026, 12, 26))  # working Saturday
    assert not business.is_working_day(ORG_ID, date(2026, 12, 27))  # Sunday
    assert not business.is_working_day(ORG_ID, date(2026, 12, 28))
    assert business.is_working_day(ORG_ID, date(2026, 12, 28), "NORTH")
    listed = run("calendar.list", date_from="2026-12-21", date_to="2026-12-31")
    # Mon 21 – Thu 31: 9 weekdays, minus Christmas and the observed Boxing Day, plus the Saturday.
    assert listed.working_days == 8
    north = run("calendar.list", date_from="2026-12-21", date_to="2026-12-31", region_code="NORTH")
    assert north.working_days == 9


def test_set_days_upserts() -> None:
    first = run("calendar.set_days", days=[{"date": "2026-03-06", "is_working_day": False}])
    again = run(
        "calendar.set_days",
        days=[{"date": "2026-03-06", "is_working_day": False, "holiday_name": "Independence Day"}],
    )
    assert (first.created, again.updated) == (1, 1)
    (day,) = run("calendar.list", date_from="2026-03-01", date_to="2026-03-31").days
    assert day.holiday_name == "Independence Day"


def test_business_date_applies_timezone_cutoff_and_holidays() -> None:
    run("currency.upsert", code="GHS", name="Ghanaian cedi")
    run(
        "settings.update",
        reporting_timezone="Europe/London",
        business_day_cutoff="17:00:00",
    )
    run("calendar.set_days", days=[{"date": "2026-12-25", "is_working_day": False}])

    def bdate(at: datetime) -> date:
        return run("calendar.business_date", at=at.isoformat()).business_date  # type: ignore[no-any-return]

    # Before the cutoff: same day.
    assert bdate(datetime(2026, 10, 14, 16, 59, tzinfo=LONDON)) == date(2026, 10, 14)
    # At the cutoff: next day.
    assert bdate(datetime(2026, 10, 14, 17, 0, tzinfo=LONDON)) == date(2026, 10, 15)
    # Friday after the cutoff rolls over the weekend to Monday.
    assert bdate(datetime(2026, 10, 16, 18, 0, tzinfo=LONDON)) == date(2026, 10, 19)
    # Read in the reporting timezone: 23:30 UTC on 24 Dec is not yet 25 Dec in London
    # (UTC+0 in winter) but is after the cutoff, and the 25th is a holiday.
    assert bdate(datetime(2026, 12, 24, 23, 30, tzinfo=ZoneInfo("UTC"))) == date(2026, 12, 28)
    # A timestamp from another zone is converted first: 01:00 in Accra = 01:00 London (winter).
    assert bdate(datetime(2026, 12, 23, 1, 0, tzinfo=ACCRA)) == date(2026, 12, 23)


def test_without_cutoff_the_day_ends_at_midnight() -> None:
    assert business.business_date(
        ORG_ID, datetime(2026, 10, 14, 23, 59, tzinfo=ZoneInfo("UTC"))
    ) == (date(2026, 10, 14))


# ── period status ───────────────────────────────────────────────────────────


def period(to_status: str, reason: str = "") -> object:
    # Scorecards closes through its own actions (tests/test_period_close.py).
    return run(
        "period.transition",
        product="agent_sales",
        period_key="202610",
        to_status=to_status,
        reason=reason,
    )


def test_period_state_machine_and_snapshot_versions() -> None:
    with pytest.raises(Conflict, match="missing period cannot become closed"):
        period("closed")
    assert period("open").status == "open"  # type: ignore[attr-defined]
    with pytest.raises(Conflict):
        period("closed")  # must pass through closing
    period("closing")
    period("open")  # a failed pre-check returns it to open
    period("closing")
    closed = period("closed")
    assert closed.status == "closed"  # type: ignore[attr-defined]
    assert closed.snapshot_version == 1  # type: ignore[attr-defined]
    assert closed.closed_at is not None  # type: ignore[attr-defined]
    with pytest.raises(InvalidInput, match="requires a reason"):
        period("restating")
    period("restating", reason="Corrected deposits feed for Kumasi branches.")
    assert period("closed").snapshot_version == 2  # type: ignore[attr-defined]
    reasons = AuditLog.objects.filter(event="period.reason")
    assert [r.payload["reason"] for r in reasons] == [
        "Corrected deposits feed for Kumasi branches."
    ]
    (listed,) = run("period.list", product="agent_sales").periods
    assert (listed.period_key, listed.status) == ("202610", "closed")


def test_scorecards_periods_close_only_through_their_own_actions() -> None:
    run("period.transition", product="scorecards", period_key="202610", to_status="open")
    for status in ("closing", "closed", "restating"):
        with pytest.raises(Conflict, match=r"scorecard\.period\.close"):
            run("period.transition", product="scorecards", period_key="202610", to_status=status)


def test_only_admin_closes_periods() -> None:
    with pytest.raises(PermissionDenied):
        run(
            "period.transition",
            role_ctx("line_manager"),
            product="scorecards",
            period_key="202610",
            to_status="open",
        )
    run("period.list", role_ctx("line_manager"))


def test_feed_deadline_upserts() -> None:
    feed = str(uuid.uuid4())
    run(
        "period.deadline.set", feed_id=feed, period_key="202610", due_at="2026-11-05T17:00:00+00:00"
    )
    run(
        "period.deadline.set", feed_id=feed, period_key="202610", due_at="2026-11-06T17:00:00+00:00"
    )
    (deadline,) = run("period.deadline.list", period_key="202610").deadlines
    assert deadline.due_at.day == 6


# ── settings, currency, FX ──────────────────────────────────────────────────


def test_settings_validate_currency_and_timezone() -> None:
    with pytest.raises(InvalidInput, match="not an active currency"):
        run("settings.update", reporting_currency="GHS")
    run("currency.upsert", code="GHS", name="Ghanaian cedi")
    with pytest.raises(InvalidInput, match="IANA"):
        run("settings.update", reporting_timezone="Mars/Olympus")
    out = run(
        "settings.update",
        reporting_currency="GHS",
        reporting_timezone="Africa/Accra",
        business_day_cutoff="17:30:00",
    )
    assert (out.reporting_currency, out.reporting_timezone) == ("GHS", "Africa/Accra")
    assert out.business_day_cutoff == time(17, 30)
    cleared = run("settings.update", clear_cutoff=True)
    assert cleared.business_day_cutoff is None
    assert cleared.reporting_currency == "GHS"


def test_every_config_change_bumps_the_version_and_reads_do_not() -> None:
    start = run("settings.get").config_version
    run("currency.upsert", code="GHS", name="Ghanaian cedi")
    run("currency.upsert", code="USD", name="US dollar")
    run("currency.list")
    run("settings.get")
    assert run("settings.get").config_version == start + 2
    with pytest.raises(InvalidInput):
        run("settings.update", reporting_timezone="Nowhere/Nope")
    assert config_version(ORG_ID) == start + 2  # a refused change is not a change
    run("settings.update", role_ctx(dry_run=True), reporting_timezone="Africa/Accra")
    assert config_version(ORG_ID) == start + 2  # nor is a dry run


def test_fx_rates_direct_inverse_and_missing() -> None:
    run("currency.upsert", code="GHS", name="Ghanaian cedi")
    run("currency.upsert", code="USD", name="US dollar")
    with pytest.raises(InvalidInput, match="Unknown currencies"):
        run(
            "fx.set",
            rates=[
                {
                    "from_currency": "EUR",
                    "to_currency": "GHS",
                    "period_key": "202610",
                    "rate": "16.5",
                }
            ],
        )
    run(
        "fx.set",
        rates=[
            {"from_currency": "USD", "to_currency": "GHS", "period_key": "202610", "rate": "15.25"}
        ],
    )
    again = run(
        "fx.set",
        rates=[
            {"from_currency": "USD", "to_currency": "GHS", "period_key": "202610", "rate": "15.5"}
        ],
    )
    assert (again.created, again.updated) == (0, 1)
    assert fx_rate(ORG_ID, "USD", "GHS", "202610") == Decimal("15.5")
    assert convert(ORG_ID, Decimal("100"), "GHS", "USD", "202610").quantize(
        Decimal("0.0001")
    ) == Decimal("6.4516")
    assert fx_rate(ORG_ID, "GHS", "GHS", "202610") == 1
    with pytest.raises(MissingRate):
        fx_rate(ORG_ID, "USD", "GHS", "202611")
    with pytest.raises(MissingRate):
        fx_rate(ORG_ID, "USD", "GHS", "202610", "closing")
    (listed,) = run("fx.list", period_key="202610").rates
    assert listed.rate == Decimal("15.5")


def test_fx_refuses_same_currency_and_non_positive_rates() -> None:
    with pytest.raises(InvalidInput):
        run(
            "fx.set",
            rates=[
                {"from_currency": "USD", "to_currency": "USD", "period_key": "202610", "rate": "1"}
            ],
        )
    with pytest.raises(InvalidInput):
        run(
            "fx.set",
            rates=[
                {"from_currency": "USD", "to_currency": "GHS", "period_key": "202610", "rate": "0"}
            ],
        )


def test_data_steward_loads_fx_but_cannot_change_settings() -> None:
    steward = role_ctx("data_steward")
    run("currency.list", steward)
    with pytest.raises(PermissionDenied):
        run("currency.upsert", steward, code="GHS", name="Ghanaian cedi")
    with pytest.raises(PermissionDenied):
        run("settings.update", steward, reporting_timezone="Africa/Accra")
