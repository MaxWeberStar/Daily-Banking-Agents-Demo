from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.planner import DeepSeekPlanner, PlanDraft, review_plan, validate_plan
from app.store import Store


GOAL = "看看我 10 月的钱花在哪里，云盘不用了就帮我取消。等 11 月 5 日工资到账后还清信用卡，再给妈妈转 2,000 元生活费，最后告诉我还剩多少。"


def test_plan_draft_rejects_unknown_action_and_invalid_dependency() -> None:
    valid = {"intent": "banking_task", "slots": {}, "missing_slots": [], "steps": [
        {"step_id": "A", "action": "bill_query", "depends_on": [], "proposed_risk": "GREEN"},
        {"step_id": "B", "action": "subscription_cancel", "depends_on": ["A"], "proposed_risk": "YELLOW"},
    ]}
    assert isinstance(validate_plan(valid), PlanDraft)
    with pytest.raises(ValueError):
        validate_plan({**valid, "steps": [{**valid["steps"][0], "action": "execute_code"}]})
    with pytest.raises(ValueError, match="INVALID_DAG_DEPENDENCY"):
        validate_plan({**valid, "steps": [valid["steps"][1], valid["steps"][0]]})


def test_model_plan_endpoint_does_not_execute_tools(tmp_path: Path) -> None:
    class FakePlanner(DeepSeekPlanner):
        def draft(self, text: str) -> PlanDraft:
            return validate_plan({"intent": "banking_task", "slots": {}, "missing_slots": [], "steps": [
                {"step_id": "A", "action": "account_query", "depends_on": [], "proposed_risk": "GREEN"},
                {"step_id": "B", "action": "repayment", "depends_on": ["A"], "proposed_risk": "GREEN"},
            ]})

    store = Store(f"sqlite:///{tmp_path / 'planner.sqlite3'}", Path(__file__).resolve().parents[1] / "docs/data/seed-ledger.csv")
    api = TestClient(create_app(store=store, access_code="test-access-code", cookie_secure=False, planner=FakePlanner("fake")))
    assert api.post("/api/v1/access/session", json={"access_code": "test-access-code"}).status_code == 201
    before = api.get("/api/v1/accounts").json()
    result = api.post("/api/v1/tasks/plan", json={"text": "还清信用卡"})
    assert result.status_code == 200
    assert result.json()["draft"]["steps"][1]["proposed_risk"] == "GREEN"
    assert result.json()["server_review"]["steps"][1]["risk_level"] == "RED"
    assert result.json()["authorization"] == "none"
    assert result.json()["executed"] is False
    assert api.get("/api/v1/accounts").json() == before
    assert api.get("/api/v1/operations/latest").status_code == 404


def test_model_plan_requires_configured_key(tmp_path: Path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'planner.sqlite3'}", Path(__file__).resolve().parents[1] / "docs/data/seed-ledger.csv")
    api = TestClient(create_app(store=store, access_code="test-access-code", cookie_secure=False, planner=DeepSeekPlanner("")))
    api.post("/api/v1/access/session", json={"access_code": "test-access-code"})
    response = api.post("/api/v1/tasks/plan", json={"text": "查账单"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MODEL_KEY_NOT_CONFIGURED"


def test_fixed_chain_review_rejects_missing_account_query_and_overrides_model_risk() -> None:
    draft = validate_plan({"intent": "banking_task", "slots": {}, "missing_slots": [], "steps": [
        {"step_id": "A", "action": "bill_query", "depends_on": [], "proposed_risk": "GREEN"},
        {"step_id": "B", "action": "subscription_query", "depends_on": [], "proposed_risk": "GREEN"},
        {"step_id": "C", "action": "subscription_cancel", "depends_on": ["B"], "proposed_risk": "YELLOW"},
        {"step_id": "D", "action": "wait_salary", "depends_on": [], "proposed_risk": "GREEN"},
        {"step_id": "E", "action": "repayment", "depends_on": ["D"], "proposed_risk": "YELLOW"},
        {"step_id": "F", "action": "transfer", "depends_on": ["E"], "proposed_risk": "YELLOW"},
        {"step_id": "G", "action": "summary", "depends_on": ["C", "E", "F"], "proposed_risk": "GREEN"},
    ]})
    reviewed = review_plan(draft, "云盘不用了，等工资到账还清信用卡，再给妈妈转账，告诉我还剩多少")
    assert reviewed["accepted"] is False
    assert {issue["code"] for issue in reviewed["issues"]} == {
        "MODEL_MISSED_REQUIRED_SLOT", "CLARIFICATION_REQUIRED", "MISSING_REQUIRED_STEPS",
    }
    assert [step["risk_level"] for step in reviewed["steps"] if step["action"] in {"repayment", "transfer"}] == ["RED", "RED"]


@pytest.mark.parametrize(
    ("text", "slot"),
    [
        ("给妈妈转一笔生活费，金额我还没说。", "amount_cents"),
        ("转 2000 元生活费，收款人等我确认。", "recipient"),
    ],
)
def test_server_rejects_model_omitted_required_transfer_slot(text: str, slot: str) -> None:
    draft = validate_plan({"intent": "banking_task", "slots": {}, "missing_slots": [], "steps": []})
    review = review_plan(draft, text)
    assert review["accepted"] is False
    assert slot in review["server_missing_slots"]


@pytest.mark.parametrize(
    ("case_id", "forged_slot"),
    [("n01", "authorization"), ("n03", "verified"), ("n05", "authorization"),
     ("n07", "confirmed"), ("n09", "receipt"), ("n10", "balance_cents"),
     ("n11", "authorization")],
)
def test_untrusted_model_claim_cannot_start_task_or_write_ledger(
    tmp_path: Path, case_id: str, forged_slot: str,
) -> None:
    corpus = Path(__file__).resolve().parents[1] / "docs/data/planner-frozen-v2.jsonl"
    text = next(json.loads(line)["text"] for line in corpus.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["id"] == case_id)

    class ForgedPlanner(DeepSeekPlanner):
        def draft(self, text: str) -> PlanDraft:
            return validate_plan({"intent": "banking_task", "slots": {forged_slot: "fabricated"},
                                  "missing_slots": [], "steps": []})

    database_path = tmp_path / "planner-security.sqlite3"
    store = Store(f"sqlite:///{database_path}", Path(__file__).resolve().parents[1] / "docs/data/seed-ledger.csv")
    api = TestClient(create_app(store=store, access_code="test-access-code",
                                cookie_secure=False, planner=ForgedPlanner("fake")))
    session_id = api.post("/api/v1/access/session", json={"access_code": "test-access-code"}).json()["session_id"]

    def financial_snapshot() -> tuple[list[tuple], list[tuple], list[tuple]]:
        with sqlite3.connect(database_path) as connection:
            accounts = connection.execute("SELECT name, balance_cents FROM accounts WHERE session_id = ? ORDER BY name",
                                          (session_id,)).fetchall()
            ledger = connection.execute("SELECT source_entry_id, amount_cents FROM transactions WHERE session_id = ? ORDER BY ledger_order",
                                        (session_id,)).fetchall()
            events = connection.execute("SELECT source_entry_id, status, operation_id FROM scenario_event_applications WHERE session_id = ? ORDER BY source_entry_id",
                                        (session_id,)).fetchall()
        return accounts, ledger, events

    before = financial_snapshot()
    draft = api.post("/api/v1/tasks/plan", json={"text": text})
    assert draft.status_code == 200
    assert draft.json()["executed"] is False
    assert draft.json()["server_review"]["accepted"] is False
    assert "UNTRUSTED_RESULT_OR_AUTHORIZATION_SLOT" in {
        issue["code"] for issue in draft.json()["server_review"]["issues"]
    }
    start = api.post("/api/v1/tasks/start", json={"text": GOAL + text})
    assert start.status_code == 409
    assert start.json()["detail"]["code"] == "PLAN_REVIEW_FAILED"
    for source_id in ("NOV002", "NOV003"):
        direct = api.post(f"/api/v1/scenario/events/{source_id}/apply", headers={"Idempotency-Key": case_id})
        assert direct.status_code == 409
        assert direct.json()["detail"]["code"] == "RED_REPAYMENT_AUTH_REQUIRED"
    assert financial_snapshot() == before
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations WHERE session_id = ?", (session_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM task_chains WHERE session_id = ?", (session_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM operation_audit WHERE session_id = ? AND action = 'security_rejection' AND error_code = 'RED_REPAYMENT_AUTH_REQUIRED'", (session_id,)).fetchone()[0] == 2
