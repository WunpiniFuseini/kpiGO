"""Source connections and adapters, credentials, cadences and the standalone validator."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from django.db import connection as django_connection

from kpigo.ingestion import crypto
from kpigo.ingestion import validator as v
from kpigo.ingestion.models import Connection, CredentialSecret, FactActualMonthly
from kpigo.ingestion.schedule import is_due, parse_cron
from kpigo.platform.models import AuditLog
from tests.conftest import run
from tests.ingestion_support import MONTHLY, PERIOD, csv_text, monthly_rows, world

VALIDATOR = Path(v.__file__)


# ── credentials ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_connection_password_is_encrypted_and_never_returned() -> None:
    out = run(
        "connection.create",
        name="dw",
        driver="mysql",
        host="dw.bank.local",
        database="warehouse",
        username="kpigo_ro",
        password="s3cret-pass",
        statement_timeout_seconds=60,
    )
    assert out.has_password and out.port == 3306 and out.statement_timeout_seconds == 60
    assert "s3cret" not in out.model_dump_json()
    stored = Connection.objects.get(name="dw")
    assert stored.secret_ref is not None
    secret = CredentialSecret.objects.get(secret_id=stored.secret_ref)
    assert "s3cret" not in secret.ciphertext
    assert crypto.decrypt(secret.ciphertext) == "s3cret-pass"
    assert "s3cret" not in json.dumps(list(AuditLog.objects.values_list("payload", flat=True)))
    assert "s3cret" not in run("connection.list").model_dump_json()

    run("connection.update", name="dw", password="rotated", row_cap=10)
    secret.refresh_from_db()
    assert crypto.decrypt(secret.ciphertext) == "rotated"
    assert CredentialSecret.objects.count() == 1


def test_credential_keys_rotate_and_production_needs_one(settings: Any) -> None:
    old, new = crypto.generate_key(), crypto.generate_key()
    settings.KPIGO_CREDENTIAL_KEYS = [old]
    token = crypto.encrypt("pw")
    settings.KPIGO_CREDENTIAL_KEYS = [new, old]
    assert crypto.decrypt(token) == "pw"
    settings.KPIGO_CREDENTIAL_KEYS = [new]
    with pytest.raises(crypto.CredentialKeyMissing):
        crypto.decrypt(token)
    settings.KPIGO_CREDENTIAL_KEYS = []
    settings.KPIGO_CREDENTIAL_DEV_KEY = False
    with pytest.raises(crypto.CredentialKeyMissing, match="KPIGO_CREDENTIAL_KEYS"):
        crypto.encrypt("pw")


# ── object names: never client SQL ──────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "kpi.v; DROP TABLE x",
        "kpi.v--",
        'kpi."v"',
        "kpi.v/*x*/",
        "(SELECT 1)",
        "a.b.c.d",
        "",
        "kpi..v",
        "kpi.v x",
        "1kpi.v",
    ],
)
def test_object_names_that_are_not_identifiers_are_refused(name: str) -> None:
    with pytest.raises(ValueError):
        v.parse_object_name(name)


def test_generated_sql_quotes_identifiers_per_dialect() -> None:
    cols = ["metric_code", "actual_value"]
    assert v.select_sql("postgres", "kpi.v_actuals", cols) == (
        'SELECT "metric_code", "actual_value" FROM "kpi"."v_actuals"'
    )
    assert (
        v.select_sql("mysql", "dw.v", cols) == "SELECT `metric_code`, `actual_value` FROM `dw`.`v`"
    )
    assert v.select_sql("sqlserver", "db.dbo.v", cols).endswith("FROM [db].[dbo].[v]")
    assert v.select_sql("oracle", "KPI.V", cols).endswith('FROM "KPI"."V"')
    assert v.describe_sql("postgres", "v") == 'SELECT * FROM "v" WHERE 1 = 0'


# ── adapters against fake drivers (CI has no SQL Server, Oracle or MySQL) ────


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def execute(self, sql: str) -> None:
        self.conn.statements.append(sql)
        if sql.endswith("WHERE 1 = 0"):
            self.description = [(c,) for c in self.conn.columns]
        elif sql.startswith("SELECT "):
            self._rows = list(self.conn.rows)

    def fetchmany(self, n: int) -> list[tuple[Any, ...]]:
        batch, self._rows = self._rows[:n], self._rows[n:]
        return batch

    def close(self) -> None:
        pass


class FakeConn:
    def __init__(self, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
        self.columns = columns
        self.rows = rows
        self.statements: list[str] = []
        self.closed = False
        self.call_timeout: int | None = None

    def cursor(self, name: str | None = None) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.kwargs: dict[str, Any] = {}

    def connect(self, **kwargs: Any) -> FakeConn:
        self.kwargs = kwargs
        return self.conn


COLUMNS = ["METRIC_CODE", "SUBJECT_REF", "PERIOD_KEY", "ACTUAL_VALUE", "EXTRA"]
# What the database returns for the selected columns.
ROWS = [("total_deposits", "E1", "202609", Decimal("10.5000"))] * 3


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeDriver, FakeConn]]:
    conn = FakeConn(COLUMNS, list(ROWS))
    driver = FakeDriver(conn)
    monkeypatch.setattr(v, "_driver_module", lambda name: driver)
    yield driver, conn


def spec(driver: str) -> v.SourceSpec:
    return v.SourceSpec(
        driver=driver,
        host="db",
        port=v.DEFAULT_PORTS[driver],
        database="dw",
        username="ro",
        password="pw",
        statement_timeout_seconds=42,
    )


@pytest.mark.parametrize("driver", ["mysql", "sqlserver", "oracle", "postgres"])
def test_each_adapter_reads_only_template_columns_under_a_timeout(
    driver: str, fake: tuple[FakeDriver, FakeConn]
) -> None:
    module, conn = fake
    table = v.read_source(spec(driver), "kpi.v_actuals", v.TEMPLATES["actual_monthly"], row_cap=10)
    assert table.header == ["metric_code", "subject_ref", "period_key", "actual_value"]
    assert table.rows[0] == ("total_deposits", "E1", "202609", "10.5000")
    select = conn.statements[-1]
    assert "EXTRA" not in select and "currency_code" not in select.lower()
    assert conn.closed
    kwargs = module.kwargs
    assert "pw" in kwargs.values()
    if driver == "mysql":
        assert "SET SESSION MAX_EXECUTION_TIME = 42000" in conn.statements
        assert "SET SESSION TRANSACTION READ ONLY" in conn.statements
        assert kwargs["read_timeout"] == 42
    elif driver == "sqlserver":
        assert kwargs["timeout"] == 42 and kwargs["port"] == "1433"
        assert select.endswith("FROM [kpi].[v_actuals]")
    elif driver == "oracle":
        assert conn.call_timeout == 42000 and kwargs["dsn"] == "db:1521/dw"
    else:
        assert "statement_timeout=42000" in kwargs["options"]
        assert "default_transaction_read_only=on" in kwargs["options"]


def test_row_cap_stops_a_pull(fake: tuple[FakeDriver, FakeConn]) -> None:
    with pytest.raises(v.RowCapExceeded):
        v.read_source(spec("mysql"), "kpi.v", v.TEMPLATES["actual_monthly"], row_cap=2)


def test_driver_errors_do_not_leak_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def connect(self, **kwargs: Any) -> None:
            raise RuntimeError("login failed\nfor password=pw")

    monkeypatch.setattr(v, "_driver_module", lambda name: Broken())
    with pytest.raises(v.SourceError) as err:
        v.open_source(spec("oracle"))
    assert "password" not in str(err.value) and "RuntimeError" in str(err.value)
    assert "pw" not in repr(spec("oracle"))


# ── Postgres for real: a client view in a separate schema ───────────────────


@pytest.fixture
def client_view(db: None) -> Iterator[str]:
    """A 'client database' view, committed through its own connection."""
    settings = django_connection.settings_dict
    schema = f"client_{uuid.uuid4().hex[:8]}"
    dsn = dict(
        host=settings["HOST"],
        port=settings["PORT"],
        dbname=settings["NAME"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        autocommit=True,
    )
    with psycopg.connect(**dsn) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
        conn.execute(
            f"CREATE TABLE {schema}.actuals (metric_code text, subject_ref text, "
            "period_key text, actual_value numeric(18,4), currency_code text, note text)"
        )
        for staff_no, value in (("E1", "100.5"), ("E2", "0"), ("E3", "250")):
            conn.execute(
                f"INSERT INTO {schema}.actuals VALUES (%s, %s, %s, %s, 'GHS', 'n')",
                ["total_deposits", staff_no, PERIOD, value],
            )
        conn.execute(f"CREATE VIEW {schema}.v_actual_monthly AS SELECT * FROM {schema}.actuals")
    try:
        yield schema
    finally:
        with psycopg.connect(**dsn) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.mark.django_db
def test_pull_feed_from_postgres_dry_runs_then_loads(client_view: str) -> None:
    world()
    db = django_connection.settings_dict
    run(
        "connection.create",
        name="dw",
        driver="postgres",
        host=db["HOST"] or "localhost",
        port=int(db["PORT"] or 5432),
        database=db["NAME"],
        username=db["USER"],
        password=db["PASSWORD"],
    )
    tested = run(
        "connection.test",
        name="dw",
        source_object=f"{client_view}.v_actual_monthly",
        template="actual_monthly",
    )
    assert tested.ok and "note" in tested.columns
    assert not run("connection.test", name="dw", source_object=f"{client_view}.nope").ok

    run(
        "feed.register",
        name="pulled",
        template="actual_monthly",
        mode="pull",
        connection="dw",
        source_object=f"{client_view}.v_actual_monthly",
        cadence_cron="0 6 * * *",
    )
    dry = run("feed.dry_run", feed="pulled")
    assert dry.passed and dry.rows_read == 3 and dry.source_name.endswith("v_actual_monthly")
    assert FactActualMonthly.objects.count() == 0
    loaded = run("feed.run", feed="pulled")
    assert loaded.outcome == "success" and FactActualMonthly.objects.count() == 3
    assert run("feed.run", feed="pulled").idempotent

    run("connection.update", name="dw", password="wrong")
    failed = run("feed.dry_run", feed="pulled")
    assert failed.outcome == "failed" and "connect" in (failed.error or "").lower()


# ── cadences ────────────────────────────────────────────────────────────────


def test_cadence_is_due_once_per_scheduled_minute() -> None:
    zone = ZoneInfo("Africa/Accra")
    at_six = datetime(2026, 10, 5, 6, 0, 30, tzinfo=UTC)
    assert is_due("0 6 * * *", at_six - timedelta(minutes=5), at_six, zone)
    assert not is_due("0 6 * * *", at_six, at_six + timedelta(minutes=30), zone)
    assert is_due("0 6 * * *", None, at_six, zone)
    assert not is_due("0 6 * * 0", at_six - timedelta(minutes=5), at_six, zone)  # a Monday
    with pytest.raises(ValueError):
        parse_cron("61 * * * *")
    with pytest.raises(ValueError):
        parse_cron("* * *")


# ── the standalone validator ────────────────────────────────────────────────


def test_validator_imports_only_the_standard_library() -> None:
    tree = ast.parse(VALIDATOR.read_text())
    imported = {
        (node.module or "").split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    imported.discard("")
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


def _standalone(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    copy = tmp_path / "validator.py"
    shutil.copy(VALIDATOR, copy)
    return subprocess.run(
        [sys.executable, "-I", str(copy), *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
    )


def test_standalone_validator_runs_from_a_copied_file(tmp_path: Path) -> None:
    good = tmp_path / "good.csv"
    good.write_text(csv_text(MONTHLY, monthly_rows()))
    done = _standalone(tmp_path, "--template", "actual_monthly", "--file", str(good))
    assert done.returncode == 0, done.stderr
    assert "PASS" in done.stdout and "skipped" in done.stdout

    rows = [*monthly_rows(), ["total_deposits", "E1", PERIOD, "1", "GHS"]]
    bad = tmp_path / "bad.csv"
    bad.write_text(csv_text(MONTHLY, rows))
    report = tmp_path / "rejections.csv"
    done = _standalone(
        tmp_path, "--template", "actual_monthly", "--file", str(bad), "--report", str(report)
    )
    assert done.returncode == 1 and "duplicate_at_grain" in report.read_text()


@pytest.mark.django_db
def test_standalone_validator_with_a_contract_matches_the_server(tmp_path: Path) -> None:
    world()
    run("feed.register", name="monthly", template="actual_monthly", mode="upload")
    contract = run("feed.contract.export", name="monthly", period_from=PERIOD, period_to=PERIOD)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract.contract))
    rows = [*monthly_rows({"E1": "1", "E2": "2"}), ["total_deposits", "E99", PERIOD, "1", "GHS"]]
    data = tmp_path / "load.csv"
    data.write_text(csv_text(MONTHLY, rows))
    done = _standalone(
        tmp_path,
        "--template",
        "actual_monthly",
        "--file",
        str(data),
        "--contract",
        str(path),
        "--json",
    )
    assert done.returncode == 1, done.stderr
    local = json.loads(done.stdout)
    from tests.ingestion_support import dry

    server = dry("monthly", MONTHLY, rows)
    assert local["passed"] is server.passed is False
    assert local["errors"] == sum(g["error"] for g in server.gates.values())
    assert local["warnings"] == server.warnings  # the missing E3 row warns on both
    assert local["content_hash"] == server.content_hash


def test_xlsx_files_read_like_csv(tmp_path: Path) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Metric_Code", "subject_ref", "period_key", "actual_value"])
    sheet.append(["total_deposits", "E1", "202609", 12.5])
    sheet.append([None, None, None, None])
    path = tmp_path / "x.xlsx"
    book.save(path)
    table = v.read_file("x.xlsx", path.read_bytes(), row_cap=10)
    assert table.header[0] == "metric_code" and table.rows == [
        ("total_deposits", "E1", "202609", "12.5")
    ]
    with pytest.raises(v.SourceError):
        v.read_file("x.txt", b"a", row_cap=10)
