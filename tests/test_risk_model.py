from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.risk_model import score_risk
from app.store import Store


def test_simulated_score_is_explainable_and_advisory() -> None:
    low = score_risk({"new_recipient": 0.0, "amount_ratio": 0.05,
                      "short_frequency": 0.0, "failed_codes": 0.0})
    high = score_risk({"new_recipient": 1.0, "amount_ratio": 1.2,
                       "short_frequency": 3.0, "failed_codes": 2.0})
    assert high["score"] > low["score"]
    assert len(high["reasons"]) == 4
    assert "不是欺诈概率" in str(high["meaning"])


def test_risk_score_does_not_replace_red_transfer_gate(tmp_path: Path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'risk.sqlite3'}", Path(__file__).resolve().parents[1] / "docs/data/seed-ledger.csv")
    api = TestClient(create_app(store=store, access_code="test-access-code", cookie_secure=False))
    api.post("/api/v1/access/session", json={"access_code": "test-access-code"})
    api.post("/api/v1/scenario/advance")
    api.post("/api/v1/scenario/events/NOV001/apply", headers={"Idempotency-Key": "salary"})
    repayment = api.post("/api/v1/operations/repayment/prepare", headers={"Idempotency-Key": "repayment"}).json()
    api.post(f"/api/v1/operations/{repayment['operation_id']}/verify", json={"code": "246810"})
    api.post(f"/api/v1/operations/{repayment['operation_id']}/confirm", json={"summary_hash": repayment["summary_hash"]})
    api.post(f"/api/v1/operations/{repayment['operation_id']}/execute")
    prepared = api.post("/api/v1/operations/transfer/prepare", json={
        "text": "给妈妈转 2000 元生活费", "idempotency_key": "red-transfer",
    })
    assert prepared.status_code == 201
    body = prepared.json()
    assert body["summary"]["risk_level"] == "RED"
    assert body["risk_advice"]["model_version"] == "simulated-risk-v1"
    assert api.post(f"/api/v1/operations/{body['operation_id']}/execute").status_code == 403


@pytest.mark.parametrize("score", [0.01, 0.99])
def test_high_and_low_advice_cannot_change_red_gate_or_session_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, score: float,
) -> None:
    database_path = tmp_path / "risk-gate.sqlite3"
    store = Store(f"sqlite:///{database_path}", Path(__file__).resolve().parents[1] / "docs/data/seed-ledger.csv")
    api = TestClient(create_app(store=store, access_code="test-access-code", cookie_secure=False))
    api.post("/api/v1/access/session", json={"access_code": "test-access-code"})
    before = api.get("/api/v1/accounts").json()["accounts"]
    monkeypatch.setattr("app.main.score_risk", lambda features: {**score_risk(features), "score": score})

    prepared = api.post("/api/v1/operations/transfer/prepare", json={
        "text": "给妈妈转 ¥1,500 生活费", "idempotency_key": f"risk-score-{score}",
    })
    assert prepared.status_code == 201
    body = prepared.json()
    assert body["summary"]["risk_level"] == "RED"
    assert body["risk_advice"]["score"] == score
    assert "仅为虚构案例训练的关注提示" in body["risk_advice"]["meaning"]
    assert "不是欺诈概率或授权判定" in body["risk_advice"]["meaning"]
    assert "实验性模拟关注分（非欺诈概率）" in api.get("/operations").text
    assert "独立虚构留出集未优于固定规则" in api.get("/operations").text
    operation_id = body["operation_id"]
    assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 403
    for expected in (401, 401, 423):
        assert api.post(f"/api/v1/operations/{operation_id}/verify", json={"code": "000000"}).status_code == expected
    assert api.post(f"/api/v1/operations/{operation_id}/execute").status_code == 423
    assert api.get("/api/v1/accounts").json()["accounts"] == before
