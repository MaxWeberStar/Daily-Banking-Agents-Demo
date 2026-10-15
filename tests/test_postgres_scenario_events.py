from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store
from test_task_chain import FixedPlanner, GOAL


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = PROJECT_ROOT / "docs/data/seed-ledger.csv"


@pytest.fixture(scope="module")
def postgres_runtime() -> Iterator[tuple[TestClient, Store]]:
    database_url = os.environ.get("CR01_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set CR01_TEST_DATABASE_URL to a disposable cr01_validation_* database")
    with psycopg.connect(database_url, prepare_threshold=None) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        if not database_name.startswith("cr01_validation_"):
            pytest.fail("PostgreSQL integration tests require a disposable cr01_validation_* database")
        migration_present = connection.execute(
            "SELECT to_regclass('public.scenario_event_applications')"
        ).fetchone()[0]
        if not migration_present:
            pytest.fail("apply migrations/001..009 to the disposable database before running this test")

    original_connection = Store._connection

    @contextmanager
    def runtime_connection(self: Store, session_id: str | None = None) -> Iterator[Any]:
        with original_connection(self, session_id) as connection:
            connection.execute("SET ROLE demo_runtime")
            yield connection

    Store._connection = runtime_connection  # type: ignore[method-assign]
    store = Store(database_url, LEDGER_PATH)
    client = TestClient(create_app(store=store, access_code="test-access-code", cookie_secure=False))
    try:
        yield client, store
    finally:
        client.close()
        Store._connection = original_connection  # type: ignore[method-assign]


def test_postgres_migration_rls_transactions_unique_constraint_and_concurrent_event_application(
    postgres_runtime: tuple[TestClient, Store],
) -> None:
    api, store = postgres_runtime
    api_a, api_b = TestClient(api.app), TestClient(api.app)
    session_a = api_a.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]
    session_b = api_b.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]
    assert next(row["balance_cents"] for row in api_a.get("/api/v1/accounts").json()["accounts"]
                if row["name"] == "借记卡6688") == 182400

    assert api_a.post("/api/v1/scenario/advance").status_code == 200
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda _: store.apply_scenario_event(session_a, "NOV001", "concurrent-nov001"), range(4)
        ))
    assert all(error is None for _, error in results)
    payroll_receipts = [receipt for receipt, _ in results]
    assert all(receipt == payroll_receipts[0] for receipt in payroll_receipts)
    assert len({receipt["operation_id"] for receipt in payroll_receipts}) == 1
    assert next(row["balance_cents"] for row in api_a.get("/api/v1/accounts").json()["accounts"]
                if row["name"] == "借记卡6688") == 842400

    assert api_a.post("/api/v1/scenario/events/NOV002/apply", headers={"Idempotency-Key": "direct"}).status_code == 409
    prepared = api_a.post("/api/v1/operations/repayment/prepare", headers={"Idempotency-Key": "pg-repayment"})
    assert prepared.status_code == 201
    operation = prepared.json()
    operation_id = operation["operation_id"]
    assert api_a.post(f"/api/v1/operations/{operation_id}/execute").status_code == 403
    assert api_a.post(f"/api/v1/operations/{operation_id}/verify", json={"code": "246810"}).status_code == 200
    assert api_a.post(f"/api/v1/operations/{operation_id}/confirm", json={"summary_hash": operation["summary_hash"]}).status_code == 200
    repayment = api_a.post(f"/api/v1/operations/{operation_id}/execute")
    assert repayment.status_code == 200
    assert repayment.json()["receipt"]["source_entry_ids"] == ["NOV002", "NOV003"]
    accounts_a = api_a.get("/api/v1/accounts").json()["accounts"]
    assert next(row["balance_cents"] for row in accounts_a if row["name"] == "借记卡6688") == 558400
    assert next(row["balance_cents"] for row in accounts_a if row["name"] == "虚构信用卡4321") == 0
    assert next(row["balance_cents"] for row in api_b.get("/api/v1/accounts").json()["accounts"]
                if row["name"] == "借记卡6688") == 182400
    assert api_b.get("/api/v1/scenario").json()["events"][0]["status"] == "not_applied"

    with store._connection(session_a) as connection:
        visible_account_count = connection.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()["count"]
        application_rows = connection.execute(
            "SELECT source_entry_id, operation_id FROM scenario_event_applications "
            "WHERE source_entry_id IN ('NOV002', 'NOV003') ORDER BY source_entry_id"
        ).fetchall()
    assert visible_account_count == 2
    assert len(application_rows) == 2 and application_rows[0]["operation_id"] == application_rows[1]["operation_id"]

    with pytest.raises(psycopg.errors.UniqueViolation):
        with store._connection(session_a) as connection:
            connection.execute(
                "INSERT INTO transactions (id, source_entry_id, session_id, ledger_order, posting_date, posting_time, "
                "account, account_type, direction, amount_cents, description, prd_category, initial_category, allocation, "
                "payment_channel, bank_balance_after_cents, notes, entry_type, gross_amount_cents, "
                "include_in_monthly_spend, card_balance_after_cents) "
                "SELECT 'duplicate-' || source_entry_id, source_entry_id, session_id, 9999, posting_date, posting_time, account, "
                "account_type, direction, amount_cents, description, prd_category, initial_category, allocation, "
                "payment_channel, bank_balance_after_cents, notes, entry_type, gross_amount_cents, "
                "include_in_monthly_spend, card_balance_after_cents FROM transactions "
                "WHERE session_id = %s AND source_entry_id = 'NOV001'",
                (session_a,),
            )
    with store._connection(session_b) as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM accounts").fetchone()["count"] == 2
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM scenario_event_applications WHERE status = 'applied'"
        ).fetchone()["count"] == 0


def test_postgres_security_window_survives_reset_and_is_session_isolated(
    postgres_runtime: tuple[TestClient, Store],
) -> None:
    api, store = postgres_runtime
    first = TestClient(api.app)
    second = TestClient(api.app)
    session_a = first.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]
    session_b = second.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]
    for _ in range(2):
        assert first.post("/api/v1/scenario/events/NOV002/apply",
                          headers={"Idempotency-Key": "direct"}).status_code == 409
    assert first.post("/api/v1/session/reset").status_code == 200
    assert first.post("/api/v1/scenario/events/NOV002/apply",
                      headers={"Idempotency-Key": "direct"}).status_code == 423
    assert first.get("/api/v1/accounts").status_code == 200
    assert second.post("/api/v1/scenario/advance").status_code == 200
    assert second.post("/api/v1/scenario/events/NOV002/apply",
                       headers={"Idempotency-Key": "direct"}).status_code == 409
    with store._connection(session_a) as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM security_rejections").fetchone()["count"] == 0
    with store._connection(session_b) as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM security_rejections").fetchone()["count"] == 1


def test_postgres_fixed_task_chain_and_task_event_rls(
    postgres_runtime: tuple[TestClient, Store],
) -> None:
    _, store = postgres_runtime
    api = TestClient(create_app(store=store, access_code="test-access-code",
                                cookie_secure=False, planner=FixedPlanner("fake")))
    another = TestClient(api.app)
    session_a = api.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]
    session_b = another.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]
    started = api.post("/api/v1/tasks/start", json={"text": GOAL})
    assert started.status_code == 201
    task_id = started.json()["task_id"]
    assert another.get(f"/api/v1/tasks/{task_id}").status_code == 404
    assert api.post("/api/v1/scenario/advance").status_code == 200
    assert api.post("/api/v1/scenario/events/NOV001/apply", headers={"Idempotency-Key": "task-salary"}).status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 200
    prepared = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                        headers={"Idempotency-Key": "task-repay"})
    assert prepared.status_code == 201
    operation = prepared.json()
    assert api.post(f"/api/v1/operations/{operation['operation_id']}/verify",
                    json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation['operation_id']}/confirm",
                    json={"summary_hash": operation["summary_hash"]}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation['operation_id']}/execute").status_code == 200
    assert api.get(f"/api/v1/tasks/{task_id}").json()["steps"]["E"]["status"] == "ready"
    with store._connection(session_a) as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM task_events").fetchone()["count"] > 0
    with store._connection(session_b) as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM task_events").fetchone()["count"] == 0
