from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime, timedelta
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.planner import DeepSeekPlanner, PlanDraft, validate_plan
from app.store import Store


GOAL = "看看我 10 月的钱花在哪里，云盘不用了就帮我取消。等 11 月 5 日工资到账后还清信用卡，再给妈妈转 2,000 元生活费，最后告诉我还剩多少。"
LEDGER = Path(__file__).resolve().parents[1] / "docs/data/seed-ledger.csv"


class FixedPlanner(DeepSeekPlanner):
    def draft(self, text: str) -> PlanDraft:
        return validate_plan({"intent": "banking_task", "slots": {}, "missing_slots": [], "steps": [
            {"step_id": "A", "action": "bill_query", "depends_on": [], "proposed_risk": "GREEN"},
            {"step_id": "B", "action": "subscription_query", "depends_on": [], "proposed_risk": "GREEN"},
            {"step_id": "C", "action": "account_query", "depends_on": [], "proposed_risk": "GREEN"},
            {"step_id": "D", "action": "subscription_cancel", "depends_on": ["B"], "proposed_risk": "YELLOW"},
            {"step_id": "E", "action": "wait_salary", "depends_on": [], "proposed_risk": "GREEN"},
            {"step_id": "F", "action": "repayment", "depends_on": ["E"], "proposed_risk": "RED"},
            {"step_id": "G", "action": "transfer", "depends_on": ["F"], "proposed_risk": "RED"},
            {"step_id": "H", "action": "summary", "depends_on": ["D", "F", "G"], "proposed_risk": "GREEN"},
        ]})


def task_client(tmp_path: Path) -> TestClient:
    store = Store(f"sqlite:///{tmp_path / 'task.sqlite3'}", LEDGER)
    api = TestClient(create_app(store=store, access_code="test-access-code",
                                cookie_secure=False, planner=FixedPlanner("fake")))
    assert api.post("/api/v1/access/session", json={"access_code": "test-access-code"}).status_code == 201
    return api


def authorize_and_execute(api: TestClient, operation: dict[str, object]) -> dict[str, object]:
    operation_id = operation["operation_id"]
    assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 403
    assert api.post(f"/api/v1/operations/{operation_id}/verify", json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/confirm",
                    json={"summary_hash": operation["summary_hash"]}).status_code == 200
    response = api.post(f"/api/v1/operations/{operation_id}/execute")
    assert response.status_code == 200
    return response.json()["receipt"]


def advance_funds_step(api: TestClient, task_id: str, step_id: str) -> dict[str, object]:
    """把固定链推进到指定资金步骤的待授权状态，返回 prepare 结果。"""
    assert api.post(f"/api/v1/tasks/{task_id}/steps/B/execute", json={"confirmed": True}).status_code == 200
    assert api.post("/api/v1/scenario/advance").status_code == 200
    assert api.post("/api/v1/scenario/events/NOV001/apply",
                    headers={"Idempotency-Key": "salary"}).status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 200
    if step_id == "E":
        repayment = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                             headers={"Idempotency-Key": "repay"}).json()
        authorize_and_execute(api, repayment)
    prepared = api.post(f"/api/v1/tasks/{task_id}/steps/{step_id}/prepare",
                        headers={"Idempotency-Key": f"step-{step_id}"})
    assert prepared.status_code == 201
    return prepared.json()


def write_step_receipt(api: TestClient, step_id: str, receipt: dict[str, object]) -> None:
    """隔离夹具：把工具返回的未知／矛盾／失败回执写入一次性本地库。"""
    store = api.app.state.store
    session_id = api.get("/api/v1/session").json()["session_id"]
    table = "subscription_cancellations" if step_id == "B" else "operations"
    with store._connection(session_id) as connection:
        connection.execute(
            f"UPDATE {table} SET receipt = ?, status = ? WHERE session_id = ? AND id = ?",
            (json.dumps(receipt, ensure_ascii=False), receipt["status"], session_id,
             receipt["operation_id"]))


def inject_step_review(api: TestClient, task_id: str, step_id: str, receipt: dict[str, object],
                       *, from_status: str) -> None:
    """隔离夹具：注入回执后把步骤置为待核实。"""
    write_step_receipt(api, step_id, receipt)
    store = api.app.state.store
    session_id = api.get("/api/v1/session").json()["session_id"]
    assert store.set_task_step(session_id, task_id, step_id, {from_status}, "needs_review",
                               {"receipt": receipt, "injected_by": "isolated_fixture"},
                               str(receipt["operation_id"]))


def set_cloud_subscription_status(api: TestClient, status: str) -> None:
    """隔离夹具：只改一次性本地库中的云盘订阅状态。"""
    store = api.app.state.store
    session_id = api.get("/api/v1/session").json()["session_id"]
    with store._connection(session_id) as connection:
        connection.execute(
            "UPDATE subscriptions SET status = ? WHERE session_id = ? AND source_id = 'SUB-CLOUD'",
            (status, session_id),
        )


@pytest.mark.parametrize("case_id", ["n05", "n07", "n09"])
def test_cross_turn_claim_cannot_advance_protected_task_steps(tmp_path: Path, case_id: str) -> None:
    corpus = Path(__file__).resolve().parents[1] / "docs/data/planner-frozen-v2.jsonl"
    text = next(json.loads(line)["text"] for line in corpus.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["id"] == case_id)
    api = task_client(tmp_path)
    session_id = api.get("/api/v1/session").json()["session_id"]
    store = api.app.state.store
    before_accounts = store.accounts(session_id)
    before_scenario = store.scenario_state(session_id)
    started = api.post("/api/v1/tasks/start", json={"text": GOAL + text})
    assert started.status_code == 201
    task_id = started.json()["task_id"]
    assert started.json()["steps"]["D"]["status"] == "waiting"
    assert started.json()["steps"]["E"]["status"] == "waiting"
    assert api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                    headers={"Idempotency-Key": case_id + "-d"}).status_code == 409
    assert api.post(f"/api/v1/tasks/{task_id}/steps/E/prepare",
                    headers={"Idempotency-Key": case_id + "-e"}).status_code == 409
    assert store.accounts(session_id) == before_accounts
    assert store.scenario_state(session_id) == before_scenario
    with store._connection(session_id) as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations WHERE session_id = ?", (session_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE session_id = ? AND source_entry_id IN ('NOV002', 'NOV003', 'NOV004')", (session_id,)).fetchone()[0] == 0
    events = api.get(f"/api/v1/tasks/{task_id}").json()["events"]
    assert all(event["status"] != "succeeded" for event in events if event["step_id"] in {"D", "E"})


def test_fixed_task_waits_for_user_date_and_requires_independent_red_authorizations(tmp_path: Path) -> None:
    api = task_client(tmp_path)
    started = api.post("/api/v1/tasks/start", json={"text": GOAL})
    assert started.status_code == 201
    task_id = started.json()["task_id"]
    assert started.json()["steps"]["A"]["status"] == "succeeded"
    assert started.json()["steps"]["C"]["status"] == "waiting"
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 409
    assert api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare", headers={"Idempotency-Key": "repay"}).status_code == 409
    assert api.post(f"/api/v1/tasks/{task_id}/steps/B/execute", json={"confirmed": False}).status_code == 409
    cancelled = api.post(f"/api/v1/tasks/{task_id}/steps/B/execute", json={"confirmed": True})
    assert cancelled.status_code == 200
    assert cancelled.json()["receipt"]["status"] == "succeeded"
    assert api.post("/api/v1/scenario/advance").status_code == 200
    assert api.post("/api/v1/scenario/events/NOV001/apply", headers={"Idempotency-Key": "salary"}).status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 200
    repayment = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare", headers={"Idempotency-Key": "repay"})
    assert repayment.status_code == 201
    assert repayment.json()["summary"]["risk_level"] == "RED"
    assert api.post(f"/api/v1/tasks/{task_id}/pause").status_code == 200
    assert api.post(f"/api/v1/operations/{repayment.json()['operation_id']}/verify",
                    json={"code": "246810"}).status_code == 409
    assert api.post(f"/api/v1/tasks/{task_id}/resume").status_code == 200
    repay_receipt = authorize_and_execute(api, repayment.json())
    assert repay_receipt["amount_cents"] == 284000
    assert repay_receipt["summary"]["recipient"] == "虚构信用卡4321"
    transfer = api.post(f"/api/v1/tasks/{task_id}/steps/E/prepare", headers={"Idempotency-Key": "mom"})
    assert transfer.status_code == 201
    assert api.post(f"/api/v1/operations/{transfer.json()['operation_id']}/execute").status_code == 403
    assert api.post(f"/api/v1/operations/{transfer.json()['operation_id']}/verify",
                    json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{transfer.json()['operation_id']}/confirm",
                    json={"summary_hash": transfer.json()["summary_hash"]}).status_code == 200
    assert api.post(f"/api/v1/operations/{transfer.json()['operation_id']}/execute").status_code == 200
    summary = api.get(f"/api/v1/tasks/{task_id}/summary").json()
    assert summary["status"] == "verified_complete"
    assert summary["debit_balance_cents"] == 358400
    assert summary["card_balance_cents"] == 0
    completed = api.post(f"/api/v1/tasks/{task_id}/steps/F/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "verified_complete"
    assert completed.json()["steps"]["F"] == "succeeded"
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "completed"
    assert task["steps"]["F"]["status"] == "succeeded"
    assert completed.json()["steps"] == {key: step["status"] for key, step in task["steps"].items()}
    assert task["steps"]["F"]["details"]["steps"]["F"] == "succeeded"
    assert api.get("/api/v1/tasks/latest/current").json()["task_id"] == task_id
    assert api.get("/api/v1/tasks/active/current").json()["task_id"] is None
    assert task["steps"]["D"]["details"]["authorization_scope"] == "repayment_2840"
    assert task["steps"]["E"]["details"]["authorization_scope"] == "transfer_mother_2000"
    assert any(event["action"] == "transfer_verify" and event["step_id"] == "D" for event in task["events"])
    assert any(event["action"] == "transfer_verify" and event["step_id"] == "E" for event in task["events"])


def test_pause_revokes_confirmed_pending_repayment_authorization(tmp_path: Path) -> None:
    now = [datetime(2026, 10, 31, tzinfo=UTC)]
    store = Store(f"sqlite:///{tmp_path / 'pause-auth.sqlite3'}", LEDGER, clock=lambda: now[0])
    api = TestClient(create_app(store=store, access_code="test-access-code",
                                cookie_secure=False, planner=FixedPlanner("fake")))
    session_id = api.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 409
    assert store.scenario_state(session_id)["scenario_date"] == "2026-10-31"
    assert next(row for row in store.scenario_state(session_id)["events"]
                if row["source_entry_id"] == "NOV001")["status"] == "not_applied"
    assert api.post(f"/api/v1/tasks/{task_id}/pause").status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/resume").status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 409
    assert api.post("/api/v1/scenario/advance").status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 409
    assert api.post("/api/v1/scenario/events/NOV001/apply",
                    headers={"Idempotency-Key": "salary"}).status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 200
    operation = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                         headers={"Idempotency-Key": "repay"}).json()
    operation_id = operation["operation_id"]
    assert api.post(f"/api/v1/operations/{operation_id}/verify", json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/confirm",
                    json={"summary_hash": operation["summary_hash"]}).status_code == 200
    before_accounts = store.accounts(session_id)
    assert api.post(f"/api/v1/tasks/{task_id}/pause").status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 409
    assert api.post(f"/api/v1/tasks/{task_id}/resume").status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 403
    assert store.accounts(session_id) == before_accounts
    with store._connection(session_id) as connection:
        row = connection.execute("SELECT verified, confirmed FROM operations WHERE id = ?", (operation_id,)).fetchone()
        assert not row["verified"] and not row["confirmed"]
    assert api.post(f"/api/v1/operations/{operation_id}/verify", json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/confirm",
                    json={"summary_hash": operation["summary_hash"]}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 200


@pytest.mark.parametrize("step_id", ["B", "D", "E"])
def test_paused_step_can_be_cancelled_before_execution_without_its_write(tmp_path: Path, step_id: str) -> None:
    api = task_client(tmp_path)
    session_id = api.get("/api/v1/session").json()["session_id"]
    store = api.app.state.store
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    operation_id: str | None = None
    if step_id in {"D", "E"}:
        assert api.post("/api/v1/scenario/advance").status_code == 200
        assert api.post("/api/v1/scenario/events/NOV001/apply",
                        headers={"Idempotency-Key": "salary"}).status_code == 200
        assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 200
        if step_id == "E":
            repayment = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                                 headers={"Idempotency-Key": "repay"}).json()
            authorize_and_execute(api, repayment)
        prepared = api.post(f"/api/v1/tasks/{task_id}/steps/{step_id}/prepare",
                            headers={"Idempotency-Key": f"cancel-{step_id}"})
        assert prepared.status_code == 201
        operation_id = prepared.json()["operation_id"]
    before_accounts = store.accounts(session_id)
    before_scenario = store.scenario_state(session_id)
    before_subscription = next(item for item in store.subscriptions(session_id) if item["source_id"] == "SUB-CLOUD")
    assert api.post(f"/api/v1/tasks/{task_id}/pause").status_code == 200
    cancelled = api.post(f"/api/v1/tasks/{task_id}/steps/{step_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert api.post(f"/api/v1/tasks/{task_id}/resume").status_code == 200
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["steps"][step_id]["status"] == "cancelled"
    assert any(row["step_id"] == step_id and row["action"] == "step_state"
               and row["status"] == "cancelled" for row in task["events"])
    assert store.accounts(session_id) == before_accounts
    assert store.scenario_state(session_id) == before_scenario
    assert next(item for item in store.subscriptions(session_id)
                if item["source_id"] == "SUB-CLOUD")["status"] == before_subscription["status"]
    assert api.post(f"/api/v1/tasks/{task_id}/steps/{step_id}/cancel").status_code == 409
    if step_id == "B":
        assert api.post(f"/api/v1/tasks/{task_id}/steps/B/execute", json={"confirmed": True}).status_code == 409
    else:
        assert operation_id is not None
        assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 409
        cancelled_operation = api.get(f"/api/v1/operations/{operation_id}")
        assert cancelled_operation.status_code == 200
        assert cancelled_operation.json()["receipt"]["status"] == "cancelled"
    with store._connection(session_id) as connection:
        source = "NOV002" if step_id == "D" else "NOV004"
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE session_id = ? AND source_entry_id = ?",
                                  (session_id, source)).fetchone()[0] == 0
        if step_id == "B":
            assert connection.execute("SELECT COUNT(*) FROM subscription_cancellations WHERE session_id = ?",
                                      (session_id,)).fetchone()[0] == 0


def test_pause_revokes_confirmed_pending_transfer_authorization(tmp_path: Path) -> None:
    api = task_client(tmp_path)
    session_id = api.get("/api/v1/session").json()["session_id"]
    store = api.app.state.store
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    assert api.post("/api/v1/scenario/advance").status_code == 200
    assert api.post("/api/v1/scenario/events/NOV001/apply",
                    headers={"Idempotency-Key": "salary"}).status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 200
    repayment = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                         headers={"Idempotency-Key": "repay"}).json()
    authorize_and_execute(api, repayment)
    assert api.post(f"/api/v1/tasks/{task_id}/steps/D/cancel").status_code == 409
    transfer = api.post(f"/api/v1/tasks/{task_id}/steps/E/prepare",
                        headers={"Idempotency-Key": "mom"}).json()
    operation_id = transfer["operation_id"]
    assert api.post(f"/api/v1/operations/{operation_id}/verify", json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/confirm",
                    json={"summary_hash": transfer["summary_hash"]}).status_code == 200
    before_accounts = store.accounts(session_id)
    assert api.post(f"/api/v1/tasks/{task_id}/pause").status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/resume").status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 403
    assert store.accounts(session_id) == before_accounts
    assert any(item["action"] == "authorization_invalidated"
               for item in store.operation_audit(session_id, operation_id))
    with store._connection(session_id) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE source_entry_id = 'NOV004'").fetchone()[0] == 0
    assert api.post(f"/api/v1/operations/{operation_id}/verify", json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/confirm",
                    json={"summary_hash": transfer["summary_hash"]}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/E/cancel").status_code == 409


def test_fixed_task_wrong_codes_lock_d_and_e_until_fresh_authorization(tmp_path: Path) -> None:
    now = [datetime.now(UTC)]
    store = Store(f"sqlite:///{tmp_path / 'locked-task.sqlite3'}", LEDGER, clock=lambda: now[0])
    api = TestClient(create_app(store=store, access_code="test-access-code",
                                cookie_secure=False, planner=FixedPlanner("fake")))
    api.post("/api/v1/access/session", json={"access_code": "test-access-code"})
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    api.post("/api/v1/scenario/advance")
    api.post("/api/v1/scenario/events/NOV001/apply", headers={"Idempotency-Key": "salary"})
    api.post(f"/api/v1/tasks/{task_id}/steps/C/check")
    operation = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                         headers={"Idempotency-Key": "repay"}).json()
    for expected in (401, 401, 423):
        assert api.post(f"/api/v1/operations/{operation['operation_id']}/verify",
                        json={"code": "000000"}).status_code == expected
    assert api.get("/api/v1/accounts").status_code == 200
    assert api.get(f"/api/v1/tasks/{task_id}").status_code == 200
    assert api.post(f"/api/v1/operations/{operation['operation_id']}/execute").status_code == 423
    assert api.post(f"/api/v1/tasks/{task_id}/steps/E/prepare",
                    headers={"Idempotency-Key": "mom"}).status_code == 423
    assert api.get(f"/api/v1/tasks/{task_id}").json()["steps"]["E"]["status"] == "waiting"
    debit = next(item for item in api.get("/api/v1/accounts").json()["accounts"] if item["name"] == "借记卡6688")
    assert debit["balance_cents"] == 842400
    now[0] += timedelta(minutes=15)
    assert api.post(f"/api/v1/operations/{operation['operation_id']}/execute").status_code == 403
    assert api.post(f"/api/v1/operations/{operation['operation_id']}/verify",
                    json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation['operation_id']}/confirm",
                    json={"summary_hash": operation["summary_hash"]}).status_code == 200
    assert api.post(f"/api/v1/operations/{operation['operation_id']}/execute").status_code == 200
    assert api.get(f"/api/v1/tasks/{task_id}").json()["steps"]["E"]["status"] == "ready"
    events = api.get(f"/api/v1/tasks/{task_id}").json()["events"]
    assert any(item["action"] == "circuit_breaker" for item in events)
    assert any(item["action"] == "security_unlock" for item in events)


@pytest.mark.parametrize("cause", ["wrong_code", "unauthorized_execute"])
@pytest.mark.parametrize("reset_during_lock", [False, True])
def test_d_security_lock_preserves_funds_and_requires_fresh_authorization(
    tmp_path: Path, cause: str, reset_during_lock: bool,
) -> None:
    now = [datetime.now(UTC)]
    store = Store(f"sqlite:///{tmp_path / 'd-security.sqlite3'}", LEDGER, clock=lambda: now[0])
    api = TestClient(create_app(store=store, access_code="test-access-code",
                                cookie_secure=False, planner=FixedPlanner("fake")))
    session_id = api.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    assert api.post("/api/v1/scenario/advance").status_code == 200
    assert api.post("/api/v1/scenario/events/NOV001/apply",
                    headers={"Idempotency-Key": "salary"}).status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/C/check").status_code == 200
    operation = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                         headers={"Idempotency-Key": "repay"}).json()
    operation_id = operation["operation_id"]
    before_accounts = store.accounts(session_id)
    for expected in ((401, 401, 423) if cause == "wrong_code" else (403, 403, 423)):
        path = f"/api/v1/operations/{operation_id}/verify" if cause == "wrong_code" else f"/api/v1/operations/{operation_id}/execute"
        response = api.post(path, json={"code": "000000"} if cause == "wrong_code" else None)
        assert response.status_code == expected
        if expected != 423:
            now[0] += timedelta(minutes=4)
    locked_at = now[0]
    assert store.session_locked(session_id)
    assert any(row["action"] == "circuit_breaker" for row in store.operation_audit(session_id, operation_id))
    assert store.accounts(session_id) == before_accounts
    with store._connection(session_id) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE session_id = ? "
                                  "AND source_entry_id IN ('NOV002', 'NOV003', 'NOV004')", (session_id,)).fetchone()[0] == 0
    assert api.get("/api/v1/accounts").status_code == 200
    assert api.get(f"/api/v1/tasks/{task_id}").status_code == 200
    assert api.get(f"/api/v1/operations/{operation_id}").status_code == 404
    assert api.get(f"/api/v1/operations/{operation_id}/audit").status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/E/prepare",
                    headers={"Idempotency-Key": "mom"}).status_code == 423

    if reset_during_lock:
        assert api.post("/api/v1/session/reset").status_code == 200
        assert store.session_locked(session_id)
        assert api.post("/api/v1/tasks/start", json={"text": GOAL}).status_code == 423
    now[0] = locked_at + timedelta(minutes=14)
    assert api.post("/api/v1/scenario/advance").status_code == 423
    now[0] = locked_at + timedelta(minutes=15)
    assert not store.session_locked(session_id)
    with store._connection(session_id) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE session_id = ? "
                                  "AND source_entry_id IN ('NOV002', 'NOV003', 'NOV004')", (session_id,)).fetchone()[0] == 0
    if reset_during_lock:
        assert api.post("/api/v1/tasks/start", json={"text": GOAL}).status_code == 201
    else:
        assert store.accounts(session_id) == before_accounts
        assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 403
        assert api.post(f"/api/v1/operations/{operation_id}/verify", json={"code": "246810"}).status_code == 200
        assert api.post(f"/api/v1/operations/{operation_id}/confirm",
                        json={"summary_hash": operation["summary_hash"]}).status_code == 200
        assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 200


def test_fixed_task_unknown_transfer_freezes_summary_and_requests_human(tmp_path: Path) -> None:
    api = task_client(tmp_path)
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    api.post(f"/api/v1/tasks/{task_id}/steps/B/execute", json={"confirmed": True})
    api.post("/api/v1/scenario/advance")
    api.post("/api/v1/scenario/events/NOV001/apply", headers={"Idempotency-Key": "salary"})
    api.post(f"/api/v1/tasks/{task_id}/steps/C/check")
    repayment = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                         headers={"Idempotency-Key": "repay"}).json()
    authorize_and_execute(api, repayment)
    transfer = api.post(f"/api/v1/tasks/{task_id}/steps/E/prepare",
                        headers={"Idempotency-Key": "mom"}).json()
    store = api.app.state.store
    session_id = api.get("/api/v1/session").json()["session_id"]
    with store._connection(session_id) as connection:
        connection.execute("DELETE FROM scenario_event_applications WHERE session_id = ? AND source_entry_id = 'NOV004'",
                           (session_id,))
    assert api.post(f"/api/v1/operations/{transfer['operation_id']}/verify",
                    json={"code": "246810"}).status_code == 200
    assert api.post(f"/api/v1/operations/{transfer['operation_id']}/confirm",
                    json={"summary_hash": transfer["summary_hash"]}).status_code == 200
    result = api.post(f"/api/v1/operations/{transfer['operation_id']}/execute")
    assert result.status_code == 200
    assert result.json()["receipt"]["status"] == "pending_verification"
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "needs_review"
    assert task["steps"]["E"]["status"] == "needs_review"
    assert task["steps"]["F"]["status"] == "frozen"
    assert api.post(f"/api/v1/tasks/{task_id}/steps/F/complete").status_code == 409
    assert api.post(f"/api/v1/tasks/{task_id}/steps/E/reconcile",
                    json={"confirmed_reissue": True}).json()["detail"]["code"] == "RESULT_STILL_UNKNOWN"
    assert api.post(f"/api/v1/tasks/{task_id}/human").json()["status"] == "human_requested"
    debit = next(item for item in api.get("/api/v1/accounts").json()["accounts"] if item["name"] == "借记卡6688")
    assert debit["balance_cents"] == 558400


def test_b_unknown_result_freezes_summary_and_reconcile_success_resumes(tmp_path: Path) -> None:
    api = task_client(tmp_path)
    session_id = api.get("/api/v1/session").json()["session_id"]
    store = api.app.state.store
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    executed = api.post(f"/api/v1/tasks/{task_id}/steps/B/execute", json={"confirmed": True})
    assert executed.status_code == 200
    receipt = executed.json()["receipt"]
    operation_id = receipt["operation_id"]
    set_cloud_subscription_status(api, "active")
    unknown = {**receipt, "status": "pending_verification", "error_code": "UNKNOWN_TOOL_RESULT",
               "message": "结果待核实，请先查询同一操作 ID。"}
    inject_step_review(api, task_id, "B", unknown, from_status="succeeded")
    before_accounts = store.accounts(session_id)
    before_scenario = store.scenario_state(session_id)
    with store._connection(session_id) as connection:
        before_transaction_count = connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE session_id = ?", (session_id,)
        ).fetchone()[0]

    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "needs_review"
    assert task["steps"]["B"]["status"] == "needs_review"
    assert task["steps"]["F"]["status"] == "frozen"
    assert api.get("/api/v1/accounts").status_code == 200
    assert api.get(f"/api/v1/tasks/{task_id}").status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/F/complete").status_code == 409

    still_unknown = api.post(f"/api/v1/tasks/{task_id}/steps/B/reconcile",
                             json={"confirmed_reissue": False})
    assert still_unknown.status_code == 409
    assert still_unknown.json()["detail"] == {"code": "RESULT_STILL_UNKNOWN",
                                              "operation_id": operation_id}
    assert store.accounts(session_id) == before_accounts
    assert store.scenario_state(session_id) == before_scenario
    assert next(item for item in store.subscriptions(session_id)
                if item["source_id"] == "SUB-CLOUD")["status"] == "active"
    with store._connection(session_id) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE session_id = ?",
                                  (session_id,)).fetchone()[0] == before_transaction_count
    events = api.get(f"/api/v1/tasks/{task_id}").json()["events"]
    assert any(event["step_id"] == "B" and event["action"] == "reconcile"
               and event["status"] == "still_unknown" for event in events)

    proven = {**unknown, "status": "succeeded", "message": "模拟订阅已取消。"}
    proven.pop("error_code", None)
    write_step_receipt(api, "B", proven)
    set_cloud_subscription_status(api, "cancelled")
    reconciled = api.post(f"/api/v1/tasks/{task_id}/steps/B/reconcile",
                          json={"confirmed_reissue": False})
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "succeeded"
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "active"
    assert task["steps"]["B"]["status"] == "succeeded"
    assert task["steps"]["F"]["status"] == "waiting"
    assert store.accounts(session_id) == before_accounts
    assert store.scenario_state(session_id) == before_scenario


def test_b_confirmed_failure_requires_user_consent_before_reissue(tmp_path: Path) -> None:
    api = task_client(tmp_path)
    session_id = api.get("/api/v1/session").json()["session_id"]
    store = api.app.state.store
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    executed = api.post(f"/api/v1/tasks/{task_id}/steps/B/execute", json={"confirmed": True})
    original_operation_id = executed.json()["receipt"]["operation_id"]
    set_cloud_subscription_status(api, "active")
    failed = {**executed.json()["receipt"], "status": "failed",
              "message": "模拟取消失败，请待核实。"}
    inject_step_review(api, task_id, "B", failed, from_status="succeeded")

    proved = api.post(f"/api/v1/tasks/{task_id}/steps/B/reconcile",
                      json={"confirmed_reissue": False})
    assert proved.status_code == 200
    assert proved.json()["status"] == "failed_confirmed"
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "needs_review"
    assert task["steps"]["B"]["status"] == "needs_review"
    assert task["steps"]["B"]["operation_id"] == original_operation_id

    authorized = api.post(f"/api/v1/tasks/{task_id}/steps/B/reconcile",
                          json={"confirmed_reissue": True})
    assert authorized.status_code == 200
    assert authorized.json()["status"] == "ready"
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "active"
    assert task["steps"]["B"]["status"] == "ready"
    assert task["steps"]["B"]["operation_id"] is None
    assert task["steps"]["B"]["details"] == {"reissue_authorized_by": "user"}
    assert task["steps"]["F"]["status"] == "waiting"

    retried = api.post(f"/api/v1/tasks/{task_id}/steps/B/execute", json={"confirmed": True})
    assert retried.status_code == 200
    new_receipt = retried.json()["receipt"]
    assert new_receipt["status"] == "succeeded"
    assert new_receipt["operation_id"] != original_operation_id
    assert next(item for item in store.subscriptions(session_id)
                if item["source_id"] == "SUB-CLOUD")["status"] == "cancelled"
    old_audit = api.get(f"/api/v1/operations/{original_operation_id}/audit")
    assert old_audit.status_code == 200
    assert any(event["status"] == "reissue_authorized"
               and event["receipt"]["status"] == "failed"
               for event in old_audit.json()["events"])


def test_d_unknown_result_freezes_downstream_and_reconcile_success_resumes(tmp_path: Path) -> None:
    api = task_client(tmp_path)
    session_id = api.get("/api/v1/session").json()["session_id"]
    store = api.app.state.store
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    prepared = advance_funds_step(api, task_id, "D")
    receipt = authorize_and_execute(api, prepared)
    operation_id = receipt["operation_id"]
    unknown = {**receipt, "status": "pending_verification", "error_code": "UNKNOWN_TOOL_RESULT",
               "message": "结果待核实，请先查询同一操作 ID。"}
    inject_step_review(api, task_id, "D", unknown, from_status="succeeded")
    before_accounts = store.accounts(session_id)
    before_scenario = store.scenario_state(session_id)

    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "needs_review"
    assert task["steps"]["D"]["status"] == "needs_review"
    assert task["steps"]["E"]["status"] == "frozen"
    assert task["steps"]["F"]["status"] == "frozen"
    assert api.get("/api/v1/accounts").status_code == 200
    assert api.post(f"/api/v1/tasks/{task_id}/steps/E/prepare",
                    headers={"Idempotency-Key": "blocked-e"}).status_code == 409
    assert api.post(f"/api/v1/tasks/{task_id}/steps/F/complete").status_code == 409

    still_unknown = api.post(f"/api/v1/tasks/{task_id}/steps/D/reconcile",
                             json={"confirmed_reissue": False})
    assert still_unknown.status_code == 409
    assert still_unknown.json()["detail"]["code"] == "RESULT_STILL_UNKNOWN"
    assert store.accounts(session_id) == before_accounts
    assert store.scenario_state(session_id) == before_scenario
    with store._connection(session_id) as connection:
        assert connection.execute("SELECT COUNT(*) FROM transactions WHERE session_id = ? "
                                  "AND source_entry_id = 'NOV004'", (session_id,)).fetchone()[0] == 0

    write_step_receipt(api, "D", {**receipt, "status": "succeeded"})
    reconciled = api.post(f"/api/v1/tasks/{task_id}/steps/D/reconcile",
                          json={"confirmed_reissue": False})
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == "succeeded"
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "active"
    assert task["steps"]["D"]["status"] == "succeeded"
    assert task["steps"]["E"]["status"] == "ready"
    assert task["steps"]["F"]["status"] == "waiting"
    transfer = api.post(f"/api/v1/tasks/{task_id}/steps/E/prepare",
                        headers={"Idempotency-Key": "after-d-reconcile"})
    assert transfer.status_code == 201
    assert authorize_and_execute(api, transfer.json())["status"] == "succeeded"
    assert api.get(f"/api/v1/tasks/{task_id}/summary").json()["status"] == "verified_complete"


def test_d_confirmed_failure_requires_user_consent_before_reissue(tmp_path: Path) -> None:
    api = task_client(tmp_path)
    session_id = api.get("/api/v1/session").json()["session_id"]
    store = api.app.state.store
    task_id = api.post("/api/v1/tasks/start", json={"text": GOAL}).json()["task_id"]
    prepared = advance_funds_step(api, task_id, "D")
    original_operation_id = prepared["operation_id"]
    failed = {"operation_id": original_operation_id, "operation_type": "还款",
              "status": "failed", "error_code": "SIMULATED_TOOL_REJECTED",
              "message": "模拟工具未确认该操作，资金状态未改变。"}
    inject_step_review(api, task_id, "D", failed, from_status="awaiting_authorization")

    proved = api.post(f"/api/v1/tasks/{task_id}/steps/D/reconcile",
                      json={"confirmed_reissue": False})
    assert proved.status_code == 200
    assert proved.json()["status"] == "failed_confirmed"
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["steps"]["D"]["status"] == "needs_review"
    assert task["steps"]["E"]["status"] == "frozen"
    assert task["steps"]["F"]["status"] == "frozen"

    authorized = api.post(f"/api/v1/tasks/{task_id}/steps/D/reconcile",
                          json={"confirmed_reissue": True})
    assert authorized.status_code == 200
    assert authorized.json()["status"] == "ready"
    task = api.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "active"
    assert task["steps"]["D"]["status"] == "ready"
    assert task["steps"]["E"]["status"] == "waiting"
    assert task["steps"]["F"]["status"] == "waiting"

    retried = api.post(f"/api/v1/tasks/{task_id}/steps/D/prepare",
                       headers={"Idempotency-Key": "repay-after-reissue"})
    assert retried.status_code == 201
    assert retried.json()["operation_id"] != original_operation_id
    receipt = authorize_and_execute(api, retried.json())
    assert receipt["status"] == "succeeded"
    assert api.get(f"/api/v1/operations/{original_operation_id}").json()["receipt"]["status"] == "failed"
    assert next(item for item in store.accounts(session_id)
                if item["name"] == "借记卡6688")["balance_cents"] == 558400
