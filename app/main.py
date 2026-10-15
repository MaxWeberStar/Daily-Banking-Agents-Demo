from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from threading import Lock
from pathlib import Path
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.store import Store
from app.planner import DeepSeekPlanner, ModelUnavailable, review_plan
from app.risk_model import score_risk

ROOT = Path(__file__).resolve().parents[1]
COOKIE_NAME = "visitor_session"
CONTACTS = {
    "CONTACT-CHEN-ROOMMATE": {"contact_id": "CONTACT-CHEN-ROOMMATE", "name": "陈默", "relationship": "合租室友", "account_suffix": "1937"},
    "CONTACT-CHEN-COLLEAGUE": {"contact_id": "CONTACT-CHEN-COLLEAGUE", "name": "陈默", "relationship": "公司同事", "account_suffix": "6084"},
}


class AccessRequest(BaseModel):
    access_code: str


class TransferSlots(BaseModel):
    recipient: Literal["妈妈", "陈默"] | None = None
    amount_cents: int | None = Field(default=None, gt=0, strict=True)
    purpose: str | None = Field(default=None, min_length=1, max_length=80)
    contact_id: str | None = None


class TransferInput(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    contact_id: str | None = None
    slots: TransferSlots | None = None


class IntentInput(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    mode: str = Field(default="rules", pattern="^(rules|replay)$")
    contact_id: str | None = None


class PlanInput(BaseModel):
    text: str = Field(min_length=1, max_length=600)


class TaskConfirmationInput(BaseModel):
    confirmed: bool = Field(strict=True)


class TaskReconcileInput(BaseModel):
    confirmed_reissue: bool = Field(default=False, strict=True)


class IntentDraft(BaseModel):
    intent: Literal["transfer", "bill_query", "bill_correction", "subscription_query", "subscription_cancel", "refusal", "out_of_scope", "unknown"]
    slots: dict[str, str | int]
    missing_slots: list[Literal["recipient", "recipient_selection", "amount_cents", "purpose", "month", "merchant", "target_confirmation"]]
    status: Literal["recognized", "ready_for_confirmation", "clarification_required", "refused", "unsupported"]


def parse_intent(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", text.strip())
    lowered = normalized.lower()
    if any(term in normalized for term in ("忽略之前规则", "忽略规则", "账单上的指令", "转走余额")):
        return {"intent": "refusal", "slots": {}, "missing_slots": [], "status": "refused"}
    if any(term in normalized for term in ("股票", "冻结卡", "买股票")):
        return {"intent": "out_of_scope", "slots": {}, "missing_slots": [], "status": "unsupported"}

    recipient_match = re.search(r"(?:给|转给)(妈妈|母亲|陈默)", normalized)
    amount_match = re.search(r"(?<![\d.\-])([\d,]+(?:\.\d{1,2})?)\s*(?:元|块)?", normalized)
    if any(term in normalized for term in ("转", "转账")) and recipient_match:
        recipient = "妈妈" if recipient_match.group(1) == "母亲" else recipient_match.group(1)
        slots: dict[str, Any] = {"recipient": recipient}
        if amount_match and not re.search(r"几百|几千|一笔", normalized):
            slots["amount_cents"] = int(Decimal(amount_match.group(1).replace(",", "")) * 100)
        purpose_match = re.search(r"(?:元|块)\s*(?!给|向)([\u4e00-\u9fff]{2,12})(?:$|[，。！？])", normalized)
        if purpose_match:
            slots["purpose"] = purpose_match.group(1)
        missing = []
        if recipient == "陈默":
            missing.append("recipient_selection")
        if "amount_cents" not in slots or slots["amount_cents"] <= 0:
            missing.append("amount_cents")
        if "purpose" not in slots:
            missing.append("purpose")
        return {"intent": "transfer", "slots": slots, "missing_slots": missing,
                "status": "clarification_required" if missing else "ready_for_confirmation"}
    if re.search(r"(?<![\d.\-])[\d,]+(?:\.\d{1,2})?\s*元", normalized) and "生活费" in normalized:
        amount = re.search(r"(?<![\d.\-])([\d,]+(?:\.\d{1,2})?)\s*元", normalized)
        return {"intent": "transfer", "slots": {"amount_cents": int(Decimal(amount.group(1).replace(",", "")) * 100),
                "purpose": "生活费"}, "missing_slots": ["recipient"], "status": "clarification_required"}

    if "取消" in normalized and any(name in lowered or name in normalized for name in ("keep", "云盘")):
        merchant = "Keep" if "keep" in lowered else "云盘"
        return {"intent": "subscription_cancel", "slots": {"merchant": merchant},
                "missing_slots": ["target_confirmation"], "status": "clarification_required"}
    if "订阅" in normalized and "keep" in lowered:
        return {"intent": "subscription_query", "slots": {"merchant": "Keep"}, "missing_slots": [], "status": "recognized"}
    if "团建" in normalized and ("改成" in normalized or "待报销" in normalized):
        return {"intent": "bill_correction", "slots": {"merchant": "团建烧烤", "amount_cents": 318000,
                "category": "团建垫付（待报销）"}, "missing_slots": ["target_confirmation"], "status": "clarification_required"}
    if "花在哪" in normalized or "账单" in normalized or "支出" in normalized:
        month_match = re.search(r"(\d{1,2})\s*月", normalized)
        month = f"2026-{int(month_match.group(1)):02d}" if month_match else None
        return {"intent": "bill_query", "slots": {"month": month} if month else {},
                "missing_slots": [] if month else ["month"], "status": "recognized" if month else "clarification_required"}
    if "云盘" in normalized and ("扣钱" in normalized or "订阅" in normalized):
        return {"intent": "subscription_query", "slots": {"merchant": "云盘"}, "missing_slots": [], "status": "recognized"}
    return {"intent": "unknown", "slots": {}, "missing_slots": [], "status": "clarification_required"}


class VerificationInput(BaseModel):
    code: str


class ConfirmationInput(BaseModel):
    summary_hash: str


class BillCorrectionInput(BaseModel):
    confirmed: bool = Field(strict=True)


class SubscriptionCancellationInput(BaseModel):
    confirmed: bool = Field(strict=True)


from app.ui import render_page


def _page(title: str) -> str:
    routes = {"模拟账户": "/", "账户": "/accounts", "会话": "/operations",
              "账单": "/bills", "订阅": "/subscriptions", "操作状态": "/results"}
    return render_page(routes[title])


def create_app(
    store: Store | None = None,
    access_code: str | None = None,
    cookie_secure: bool | None = None,
    planner: DeepSeekPlanner | None = None,
) -> FastAPI:
    application = FastAPI(title="日常银行事务助手", docs_url=None, redoc_url=None)
    application.state.store = store
    application.state.planner = planner or DeepSeekPlanner()
    application.state.access_code = access_code or os.environ.get("DEMO_ACCESS_CODE")
    application.state.cookie_secure = cookie_secure if cookie_secure is not None else os.environ.get("COOKIE_SECURE", "true").lower() == "true"
    application.state.database_url = os.environ.get("DATABASE_URL")
    application.state.health_instance_id = uuid4().hex
    application.state.health_request_count = 0
    application.state.health_request_lock = Lock()

    @application.middleware("http")
    async def require_visitor_session(request: Request, call_next: Any) -> Response:
        open_paths = {"/api/v1/access/session", "/api/v1/health/live", "/api/v1/health/ready"}
        if request.url.path.startswith("/api/v1/") and request.url.path not in open_paths:
            try:
                current_id = session_id(request)
            except HTTPException as error:
                return JSONResponse(status_code=error.status_code, content={"detail": error.detail})
            if (request.method == "POST"
                    and request.url.path not in {"/api/v1/session/reset", "/api/v1/tasks/plan"}
                    and get_store(request).session_locked(current_id)):
                get_store(request).record_audit(current_id, str(uuid4()), "locked_request", "rejected",
                                                parameters={"path": request.url.path}, error_code="SESSION_LOCKED")
                return JSONResponse(status_code=423, content={"detail": {"code": "SESSION_LOCKED"}})
        return await call_next(request)

    def get_store(request: Request) -> Store:
        if request.app.state.store is None:
            database_url = request.app.state.database_url
            if not database_url or not database_url.startswith(("postgresql://", "postgres://")):
                raise HTTPException(503, detail={"code": "DATABASE_NOT_CONFIGURED", "message": "模拟服务尚未配置。"})
            request.app.state.store = Store(database_url, ROOT / "docs/data/seed-ledger.csv")
        return request.app.state.store

    def session_id(request: Request) -> str:
        token = request.cookies.get(COOKIE_NAME)
        store_for_request = get_store(request)
        resolved = store_for_request.session_id_for_cookie(token) if token else None
        if not resolved:
            raise HTTPException(401, detail={"code": "SESSION_REQUIRED", "message": "请先输入有效访问码进入模拟环境。"})
        return resolved

    def health_response(content: dict[str, str], status_code: int = 200) -> JSONResponse:
        with application.state.health_request_lock:
            application.state.health_request_count += 1
            request_count = application.state.health_request_count
        return JSONResponse(
            status_code=status_code,
            content={
                **content,
                "instance_id": application.state.health_instance_id,
                "request_count": request_count,
            },
            headers={
                "Cache-Control": "no-store",
                "X-App-Instance": application.state.health_instance_id,
                "X-Health-Request-Count": str(request_count),
            },
        )

    @application.get("/api/v1/health/live", include_in_schema=False)
    @application.get("/health/live")
    def health_live() -> Response:
        return health_response({"status": "ok"})

    @application.get("/api/v1/health/ready", include_in_schema=False)
    @application.get("/health/ready")
    def health_ready(request: Request) -> Response:
        try:
            get_store(request).check_ready()
        except Exception:
            return health_response({"status": "not_ready"}, status_code=503)
        return health_response({"status": "ready"})

    @application.post("/api/v1/access/session", status_code=201)
    def create_visitor_session(data: AccessRequest, request: Request, response: Response) -> dict[str, Any]:
        configured_code = request.app.state.access_code
        if not configured_code:
            raise HTTPException(503, detail={"code": "ACCESS_CODE_NOT_CONFIGURED", "message": "模拟服务尚未配置。"})
        if not hmac.compare_digest(data.access_code.encode(), configured_code.encode()):
            raise HTTPException(401, detail={"code": "INVALID_ACCESS_CODE", "message": "访问码无效，请重试。"})
        token = secrets.token_urlsafe(32)
        identifier, expires_at = get_store(request).create_session(token)
        response.set_cookie(
            COOKIE_NAME, token, httponly=True, secure=request.app.state.cookie_secure,
            samesite="lax", path="/", max_age=24 * 60 * 60,
        )
        return {"session_id": identifier, "expires_at": expires_at.isoformat(), "mode": "rules", "demo": True}

    @application.get("/api/v1/session")
    def current_session(request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        return {"session_id": current_id, "demo": True,
                "reset_generation": get_store(request).reset_generation(current_id)}

    @application.post("/api/v1/session/reset")
    async def reset_current_session(request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        if await request.body() or request.query_params:
            raise HTTPException(422, detail={"code": "RESET_TARGET_NOT_ALLOWED"})
        result = get_store(request).reset_session(current_id)
        if not result:
            raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND"})
        return {**result, "demo": True}

    @application.get("/api/v1/scenario")
    def get_scenario(request: Request) -> dict[str, Any]:
        scenario = get_store(request).scenario_state(session_id(request))
        if not scenario:
            raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND"})
        return scenario

    @application.post("/api/v1/scenario/advance")
    def advance_scenario(request: Request) -> dict[str, Any]:
        scenario, error = get_store(request).advance_scenario(session_id(request))
        if error == "SCENARIO_EVENTS_INCOMPLETE":
            raise HTTPException(409, detail={"code": error, "message": "请先完成当前模拟日期的冻结事件。"})
        if error == "SCENARIO_COMPLETE":
            raise HTTPException(409, detail={"code": error, "message": "模拟场景已到最后日期。"})
        if error == "SESSION_LOCKED":
            raise HTTPException(423, detail={"code": error})
        if not scenario:
            raise HTTPException(404, detail={"code": error or "SESSION_NOT_FOUND"})
        return scenario

    @application.post("/api/v1/scenario/events/{source_entry_id}/apply")
    def apply_scenario_event(
        source_entry_id: str, request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(422, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
        if source_entry_id in {"NOV002", "NOV003", "NOV004"}:
            error_code = "RED_TRANSFER_AUTH_REQUIRED" if source_entry_id == "NOV004" else "RED_REPAYMENT_AUTH_REQUIRED"
            locked = get_store(request).record_security_rejection(session_id(request), str(uuid4()), error_code)
            raise HTTPException(423 if locked else 409,
                                detail={"code": "SESSION_LOCKED" if locked else error_code})
        receipt, error = get_store(request).apply_scenario_event(
            session_id(request), source_entry_id, idempotency_key,
        )
        if error in {"SCENARIO_DATE_NOT_REACHED", "SCENARIO_EVENTS_INCOMPLETE", "SCENARIO_EVENT_STATE_INCONSISTENT"}:
            raise HTTPException(409, detail={"code": error})
        if error == "SESSION_LOCKED":
            raise HTTPException(423, detail={"code": error})
        if error == "IDEMPOTENCY_KEY_REUSED":
            raise HTTPException(409, detail={"code": error})
        if error in {"SCENARIO_EVENT_NOT_FOUND", "SESSION_NOT_FOUND"}:
            raise HTTPException(404, detail={"code": error})
        if error or not receipt:
            raise HTTPException(503, detail={"code": error or "EVENT_RESULT_UNKNOWN"})
        return {"receipt": receipt, "demo": True}

    @application.get("/api/v1/accounts")
    def get_accounts(request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        return {"accounts": get_store(request).accounts(current_id), "demo": True}

    @application.get("/api/v1/bills/{month}")
    def get_bill(month: str, request: Request) -> dict[str, Any]:
        if month != "2026-10":
            raise HTTPException(422, detail={"code": "BILL_MONTH_UNAVAILABLE"})
        return get_store(request).bill_summary(session_id(request), month)

    @application.post("/api/v1/bills/corrections/{source_entry_id}")
    def correct_bill_category(source_entry_id: str, data: BillCorrectionInput, request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        store = get_store(request)
        operation_id = str(uuid4())
        if source_entry_id != "TX002":
            store.record_audit(current_id, operation_id, "bill_correction", "rejected", error_code="TRANSACTION_NOT_FOUND")
            raise HTTPException(404, detail={"code": "TRANSACTION_NOT_FOUND", "operation_id": operation_id})
        if not data.confirmed:
            store.record_audit(current_id, operation_id, "bill_correction", "rejected", confirmation={"confirmed": False}, error_code="CORRECTION_CONFIRMATION_REQUIRED")
            raise HTTPException(409, detail={"code": "CORRECTION_CONFIRMATION_REQUIRED", "operation_id": operation_id})
        correction = store.correct_team_event_category(current_id, operation_id)
        if not correction:
            store.record_audit(current_id, operation_id, "bill_correction", "failed", confirmation={"confirmed": True}, error_code="TRANSACTION_NOT_FOUND")
            raise HTTPException(404, detail={"code": "TRANSACTION_NOT_FOUND", "operation_id": operation_id})
        return {"operation_id": operation_id, "correction": correction, "demo": True}

    @application.get("/api/v1/accounts/{account_id}")
    def get_account(account_id: str, request: Request) -> dict[str, Any]:
        record = get_store(request).account(session_id(request), account_id)
        if not record:
            raise HTTPException(404, detail={"code": "ACCOUNT_NOT_FOUND"})
        return {"account": record, "demo": True}

    @application.get("/api/v1/transactions/{transaction_id}")
    def get_transaction(transaction_id: str, request: Request) -> dict[str, Any]:
        record = get_store(request).transaction(session_id(request), transaction_id)
        if not record:
            raise HTTPException(404, detail={"code": "TRANSACTION_NOT_FOUND"})
        return {"transaction": record, "demo": True}

    @application.get("/api/v1/subscriptions/{subscription_id}")
    def get_subscription(subscription_id: str, request: Request) -> dict[str, Any]:
        record = get_store(request).subscription(session_id(request), subscription_id)
        if not record:
            raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND"})
        return {"subscription": record, "demo": True}

    @application.get("/api/v1/subscriptions")
    def list_subscriptions(request: Request) -> dict[str, Any]:
        return {"subscriptions": get_store(request).subscriptions(session_id(request)), "demo": True}

    @application.post("/api/v1/subscriptions/{subscription_id}/cancel")
    def cancel_subscription(subscription_id: str, data: SubscriptionCancellationInput, request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        store = get_store(request)
        result = store.cancel_subscription(current_id, subscription_id, data.confirmed)
        if result is None:
            operation_id = str(uuid4())
            store.record_audit(current_id, operation_id, "subscription_cancel", "rejected",
                               confirmation={"confirmed": data.confirmed}, error_code="SUBSCRIPTION_NOT_FOUND")
            raise HTTPException(404, detail={"code": "SUBSCRIPTION_NOT_FOUND", "operation_id": operation_id})
        return result

    @application.post("/api/v1/intents/parse")
    def parse_user_intent(data: IntentInput, request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        mode = "rules"
        merged_from_context = False
        if data.mode == "replay":
            if data.text.strip() != "给妈妈转 2000 元生活费" or data.contact_id:
                raise HTTPException(422, detail={"code": "UNKNOWN_FIXED_REPLAY"})
            result = {"intent": "transfer", "slots": {"recipient": "妈妈", "amount_cents": 200000,
                      "purpose": "生活费"}, "missing_slots": [], "status": "ready_for_confirmation"}
            mode = "fixed_replay"
        else:
            result = parse_intent(data.text)
            prior = get_store(request).intent_context(current_id)
            if data.contact_id:
                if result["intent"] != "transfer" or result["slots"].get("recipient") != "陈默" or data.contact_id not in CONTACTS:
                    raise HTTPException(422, detail={"code": "INVALID_CONTACT_SELECTION"})
                result["slots"]["contact_id"] = data.contact_id
                result["missing_slots"] = [slot for slot in result["missing_slots"] if slot != "recipient_selection"]
                result["status"] = "ready_for_confirmation" if not result["missing_slots"] else "clarification_required"
            prior_slots = prior["slots"] if prior and prior["intent"] == "transfer" else {}
            prior_incomplete = (
                not prior_slots.get("recipient")
                or (prior_slots.get("recipient") == "陈默" and not prior_slots.get("contact_id"))
                or not prior_slots.get("amount_cents")
                or not prior_slots.get("purpose")
            )
            recipient_compatible = (
                "recipient" not in result["slots"]
                or not prior_slots.get("recipient")
                or prior_slots["recipient"] == result["slots"]["recipient"]
            )
            if (prior and prior["intent"] == result["intent"] == "transfer"
                    and recipient_compatible
                    and (prior_incomplete or "recipient" not in result["slots"] or data.contact_id)):
                merged_from_context = True
                slots = {**prior_slots, **result["slots"]}
                missing = []
                if slots.get("recipient") == "陈默":
                    if not slots.get("contact_id"):
                        missing.append("recipient_selection")
                elif not slots.get("recipient"):
                    missing.append("recipient")
                if not slots.get("amount_cents"):
                    missing.append("amount_cents")
                if not slots.get("purpose"):
                    missing.append("purpose")
                result = {"intent": "transfer", "slots": slots, "missing_slots": missing,
                          "status": "clarification_required" if missing else "ready_for_confirmation"}
        result = IntentDraft.model_validate(result).model_dump()
        if result["intent"] == "refusal":
            operation_id = str(uuid4())
            get_store(request).record_audit(current_id, operation_id, "intent", "rejected",
                                            error_code="UNTRUSTED_TEXT_CANNOT_AUTHORIZE")
            result["operation_id"] = operation_id
        if mode == "rules" and result["intent"] not in {"unknown", "refusal"}:
            get_store(request).save_intent_context(current_id, result["intent"], result["slots"])
        if (result["status"] == "clarification_required"
                and result["intent"] == "subscription_cancel"
                and "target_confirmation" in result["missing_slots"]):
            merchant = result["slots"].get("merchant")
            subscription = next((item for item in get_store(request).subscriptions(current_id)
                                 if item["merchant"] == merchant and item["status"] == "active"), None)
            if subscription:
                amount = Decimal(subscription["amount_cents"]) / 100
                period = "月" if subscription["period"] == "monthly" else subscription["period"]
                message = f"将取消模拟订阅：{merchant}，¥{amount:.2f}/{period}。请确认是否取消。"
            else:
                message = f"未找到在用的 {merchant} 订阅，请重新查询。"
        elif result["status"] == "clarification_required":
            labels = {"recipient": "请明确收款人。", "recipient_selection": "请从候选收款人中选择唯一对象。",
                      "amount_cents": "请补充明确的金额。", "purpose": "请补充用途。",
                      "month": "请明确查询月份。", "merchant": "请明确商户。",
                      "target_confirmation": "已识别目标，请先确认目标。"}
            message = "".join(labels[slot] for slot in result["missing_slots"])
        elif result["intent"] == "out_of_scope":
            message = "该请求超出首版范围。可用入口：转账、账单查询、订阅查询。"
        elif result["intent"] == "refusal":
            message = "已拒绝账单文本中的操作指令；账单内容不能授权操作。"
        elif result["intent"] == "unknown":
            message = "无法确定此任务，请补充说明或选择可用场景。"
        else:
            message = ""
        candidates = list(CONTACTS.values()) if "recipient_selection" in result["missing_slots"] else []
        return {**result, "message": message, "candidates": candidates, "mode": mode,
                "merged_from_context": merged_from_context, "demo": True}

    @application.post("/api/v1/tasks/plan")
    def propose_task_plan(data: PlanInput, request: Request) -> dict[str, Any]:
        session_id(request)
        try:
            draft = request.app.state.planner.draft(data.text)
        except ModelUnavailable as error:
            raise HTTPException(503, detail={"code": str(error)}) from None
        return {"draft": draft.model_dump(), "server_review": review_plan(draft, data.text),
                "mode": "online_deepseek", "demo": True,
                "authorization": "none", "executed": False}

    def require_task(request: Request, task_id: str) -> tuple[str, Store, dict[str, Any]]:
        current_id = session_id(request)
        store = get_store(request)
        task = store.task_chain(current_id, task_id)
        if not task:
            raise HTTPException(404, detail={"code": "TASK_NOT_FOUND"})
        return current_id, store, task

    def task_receipt_matches_ledger(store: Store, current_id: str, step_id: str,
                                    operation_id: str, receipt: dict[str, Any]) -> bool:
        if receipt.get("status") != "succeeded":
            return False
        scenario = store.scenario_state(current_id)
        events = {item["source_entry_id"]: item for item in scenario["events"]} if scenario else {}
        source_ids = ("NOV002", "NOV003") if step_id == "D" else ("NOV004",)
        if any(events.get(source_id, {}).get("status") != "applied"
               or events[source_id].get("operation_id") != operation_id for source_id in source_ids):
            return False
        balances = {item["name"]: item["balance_cents"] for item in store.accounts(current_id)}
        expected_debit = 558400 if step_id == "D" else 358400
        return (balances.get("借记卡6688") == expected_debit
                and balances.get("虚构信用卡4321") == 0)

    @application.post("/api/v1/tasks/start", status_code=201)
    def start_fixed_task(data: PlanInput, request: Request) -> dict[str, Any]:
        normalized = re.sub(r"[\s,，]", "", data.text)
        required_terms = ("10月", "云盘", "11月5日", "还清信用卡", "妈妈", "2000元", "还剩多少")
        if not all(term in normalized for term in required_terms):
            raise HTTPException(422, detail={"code": "FIXED_CHAIN_GOAL_REQUIRED"})
        current_id = session_id(request)
        store = get_store(request)
        try:
            draft = request.app.state.planner.draft(data.text)
        except ModelUnavailable as error:
            raise HTTPException(503, detail={"code": str(error)}) from None
        reviewed = review_plan(draft, data.text)
        if not reviewed["accepted"]:
            raise HTTPException(409, detail={"code": "PLAN_REVIEW_FAILED", "issues": reviewed["issues"]})
        scenario = store.scenario_state(current_id)
        accounts = store.accounts(current_id)
        subscriptions = store.subscriptions(current_id)
        debit = next((item for item in accounts if item["name"] == "借记卡6688"), None)
        card = next((item for item in accounts if item["name"] == "虚构信用卡4321"), None)
        cloud = next((item for item in subscriptions if item["merchant"] == "云盘"), None)
        if (not scenario or scenario["scenario_date"] != "2026-10-31" or not debit or not card
                or debit["balance_cents"] != 182400 or card["balance_cents"] != 284000
                or not cloud or cloud["status"] != "active"):
            raise HTTPException(409, detail={"code": "FIXED_CHAIN_BASELINE_REQUIRED"})
        bill = store.bill_summary(current_id, "2026-10")
        evidence = {"bill_month": "2026-10", "bill_transaction_count": bill.get("transaction_count"),
                    "cloud_subscription_id": cloud["id"], "cloud_amount_cents": cloud["amount_cents"],
                    "cloud_status": cloud["status"], "debit_balance_cents": debit["balance_cents"],
                    "card_balance_cents": card["balance_cents"]}
        task = store.create_task_chain(current_id, {"draft": draft.model_dump(), "review": reviewed}, evidence)
        if not task:
            raise HTTPException(409, detail={"code": "ACTIVE_TASK_EXISTS"})
        return {**task, "demo": True}

    @application.get("/api/v1/tasks/{task_id}")
    def get_fixed_task(task_id: str, request: Request) -> dict[str, Any]:
        current_id, store, task = require_task(request, task_id)
        return {**task, "events": store.task_events(current_id, task_id), "demo": True}

    @application.get("/api/v1/tasks/active/current")
    def get_active_task(request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        task_id = get_store(request).active_task_id(current_id)
        return {"task_id": task_id, "demo": True}

    @application.get("/api/v1/tasks/latest/current")
    def get_latest_task(request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        task_id = get_store(request).latest_task_id(current_id)
        return {"task_id": task_id, "demo": True}

    @application.post("/api/v1/tasks/{task_id}/pause")
    def pause_fixed_task(task_id: str, request: Request) -> dict[str, Any]:
        current_id, store, task = require_task(request, task_id)
        if task["status"] != "active" or not store.set_task_status(current_id, task_id, "paused"):
            raise HTTPException(409, detail={"code": "TASK_NOT_ACTIVE"})
        return {"task_id": task_id, "status": "paused"}

    @application.post("/api/v1/tasks/{task_id}/resume")
    def resume_fixed_task(task_id: str, request: Request) -> dict[str, Any]:
        current_id, store, task = require_task(request, task_id)
        if task["status"] != "paused" or not store.set_task_status(current_id, task_id, "active"):
            raise HTTPException(409, detail={"code": "TASK_NOT_PAUSED"})
        return {"task_id": task_id, "status": "active", "message": "已恢复；执行前仍需重新核验参数和授权。"}

    @application.post("/api/v1/tasks/{task_id}/steps/C/check")
    def check_salary_for_task(task_id: str, request: Request) -> dict[str, Any]:
        current_id, store, task = require_task(request, task_id)
        if task["status"] != "active" or task["steps"]["C"]["status"] != "waiting":
            raise HTTPException(409, detail={"code": "STEP_NOT_WAITING"})
        scenario = store.scenario_state(current_id)
        states = {item["source_entry_id"]: item["status"] for item in scenario["events"]} if scenario else {}
        debit = next((item for item in store.accounts(current_id) if item["name"] == "借记卡6688"), None)
        if (not scenario or scenario["scenario_date"] < "2026-11-05" or states.get("NOV001") != "applied"
                or not debit or debit["balance_cents"] != 842400):
            raise HTTPException(409, detail={"code": "SALARY_NOT_VERIFIED"})
        store.set_task_step(current_id, task_id, "C", {"waiting"}, "succeeded",
                            {"source_entry_id": "NOV001", "debit_balance_cents": 842400})
        return {"task_id": task_id, "step_id": "C", "status": "succeeded", "demo": True}

    @application.post("/api/v1/tasks/{task_id}/steps/B/execute")
    def cancel_cloud_for_task(task_id: str, data: TaskConfirmationInput, request: Request) -> dict[str, Any]:
        current_id, store, task = require_task(request, task_id)
        if task["status"] != "active" or task["steps"]["B"]["status"] != "ready":
            raise HTTPException(409, detail={"code": "STEP_NOT_READY"})
        if not data.confirmed:
            store.record_task_event(current_id, task_id, "B", "authorization", "rejected",
                                    {"confirmed": False})
            raise HTTPException(409, detail={"code": "SUBSCRIPTION_CONFIRMATION_REQUIRED"})
        subscription_id = task["steps"]["A"]["evidence"]["cloud_subscription_id"]
        store.record_task_event(current_id, task_id, "B", "authorization", "succeeded",
                                {"authorized_by": "visitor", "subscription_id": subscription_id,
                                 "impact": "取消云盘 ¥30/月，不产生当日资金入账"})
        result = store.cancel_subscription(current_id, subscription_id, True,
                                           allow_failed_reissue=True)
        if not result:
            raise HTTPException(409, detail={"code": "SUBSCRIPTION_NOT_FOUND"})
        receipt = result["receipt"]
        current_subscription = store.subscription(current_id, subscription_id)
        step_status = ("succeeded" if receipt["status"] == "succeeded"
                       and current_subscription and current_subscription["status"] == "cancelled" else
                       "failed" if receipt["status"] == "failed" else "needs_review")
        store.set_task_step(current_id, task_id, "B", {"ready"}, step_status,
                            {"receipt": receipt, "authorized_by": "visitor",
                             "authorization_scope": "cancel_cloud_subscription"}, receipt["operation_id"])
        return {"task_id": task_id, "step_id": "B", "status": step_status, "receipt": receipt}

    @application.post("/api/v1/tasks/{task_id}/steps/{step_id}/prepare", status_code=201)
    def prepare_funds_for_task(
        task_id: str, step_id: str, request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if step_id not in {"D", "E"} or not idempotency_key:
            raise HTTPException(422, detail={"code": "INVALID_TASK_STEP_OR_KEY"})
        current_id, store, task = require_task(request, task_id)
        step = task["steps"][step_id]
        if task["status"] != "active" or step["status"] not in {"ready", "awaiting_authorization"}:
            raise HTTPException(409, detail={"code": "STEP_NOT_READY"})
        if step_id == "D":
            summary = {"recipient": "虚构信用卡4321", "amount_cents": 284000,
                       "source_account": "借记卡6688", "purpose": "归还10月虚构信用卡账单",
                       "execution_timing": "立即", "risk_level": "RED", "demo": True}
            operation_type = "repayment"
        else:
            ready, reason, _ = store.scenario_event_ready_for_transfer(current_id)
            if not ready:
                raise HTTPException(409, detail={"code": reason or "SCENARIO_EVENT_NOT_READY"})
            summary = {"recipient": "妈妈", "amount_cents": 200000,
                       "source_account": "借记卡6688", "purpose": "生活费",
                       "execution_timing": "立即", "risk_level": "RED", "demo": True}
            operation_type = "transfer"
        operation_id, _, same, summary, summary_hash = store.create_transfer(
            current_id, summary, idempotency_key, operation_type=operation_type,
            task_id=task_id, step_id=step_id,
        )
        if not operation_id or not same:
            raise HTTPException(409, detail={"code": "TASK_PREPARE_REJECTED"})
        risk_advice = None
        if step_id == "E":
            features = store.transfer_risk_features(current_id, summary["recipient"], summary["amount_cents"])
            risk_advice = score_risk(features)
        if step["status"] == "ready":
            store.set_task_step(current_id, task_id, step_id, {"ready"}, "awaiting_authorization",
                                {"summary_hash": summary_hash, "risk_advice": risk_advice}, operation_id)
        return {"task_id": task_id, "step_id": step_id, "operation_id": operation_id,
                "summary": summary, "summary_hash": summary_hash,
                "risk_advice": risk_advice, "authorization": "模拟验证码及同参数确认待完成"}

    @application.post("/api/v1/tasks/{task_id}/steps/{step_id}/cancel")
    def cancel_fixed_task_step(task_id: str, step_id: str, request: Request) -> dict[str, Any]:
        if step_id not in {"B", "D", "E"}:
            raise HTTPException(422, detail={"code": "STEP_CANNOT_BE_CANCELLED"})
        current_id, store, task = require_task(request, task_id)
        step = task["steps"][step_id]
        if task["status"] not in {"active", "paused"} or step["status"] not in {"ready", "awaiting_authorization"}:
            raise HTTPException(409, detail={"code": "STEP_ALREADY_STARTED_OR_BLOCKED"})
        operation_id = step.get("operation_id")
        if operation_id and store.cancel_transfer(current_id, operation_id) != "cancelled":
            raise HTTPException(409, detail={"code": "OPERATION_ALREADY_FINISHED"})
        if not store.set_task_step(current_id, task_id, step_id,
                                   {"ready", "awaiting_authorization"}, "cancelled",
                                   {"reason": "user_cancelled_before_execution"}, operation_id):
            raise HTTPException(409, detail={"code": "STEP_STATE_CHANGED"})
        return {"task_id": task_id, "step_id": step_id, "status": "cancelled"}

    @application.get("/api/v1/tasks/{task_id}/summary")
    def task_summary(task_id: str, request: Request) -> dict[str, Any]:
        current_id, store, task = require_task(request, task_id)
        accounts = store.accounts(current_id)
        debit = next((item for item in accounts if item["name"] == "借记卡6688"), None)
        card = next((item for item in accounts if item["name"] == "虚构信用卡4321"), None)
        receipts = {}
        for step_id in ("D", "E"):
            operation_id = task["steps"][step_id].get("operation_id")
            receipts[step_id] = store.transfer_receipt(current_id, operation_id) if operation_id else None
        verified = (task["steps"]["B"]["status"] == "succeeded"
                    and all(task["steps"][key]["status"] == "succeeded"
                            and receipts[key] and receipts[key]["status"] == "succeeded" for key in ("D", "E"))
                    and debit and debit["balance_cents"] == 358400
                    and card and card["balance_cents"] == 0)
        return {"task_id": task_id, "status": "verified_complete" if verified else "partial_or_pending",
                "steps": {key: value["status"] for key, value in task["steps"].items()},
                "debit_balance_cents": debit["balance_cents"] if debit else None,
                "card_balance_cents": card["balance_cents"] if card else None,
                "receipts": receipts, "subscription_savings_are_not_cash_inflow": True,
                "demo": True}

    @application.post("/api/v1/tasks/{task_id}/steps/F/complete")
    def complete_fixed_task(task_id: str, request: Request) -> dict[str, Any]:
        current_id, store, task = require_task(request, task_id)
        if task["status"] != "active" or task["steps"]["F"]["status"] != "ready":
            raise HTTPException(409, detail={"code": "SUMMARY_PREREQUISITES_NOT_FINAL"})
        result = task_summary(task_id, request)
        result["steps"]["F"] = "succeeded"
        store.set_task_step(current_id, task_id, "F", {"ready"}, "succeeded", result)
        store.set_task_status(current_id, task_id, "completed")
        return task_summary(task_id, request)

    @application.post("/api/v1/tasks/{task_id}/human")
    def request_human_for_task(task_id: str, request: Request) -> dict[str, Any]:
        current_id, store, task = require_task(request, task_id)
        if task["status"] != "needs_review":
            raise HTTPException(409, detail={"code": "HUMAN_HANDOFF_NOT_REQUIRED"})
        store.record_task_event(current_id, task_id, None, "human_handoff", "requested",
                                {"reason": "result_still_unknown"})
        return {"task_id": task_id, "status": "human_requested", "message": "已记录人工处理请求；未重试资金写入。"}

    @application.post("/api/v1/tasks/{task_id}/steps/{step_id}/reconcile")
    def reconcile_task_step(task_id: str, step_id: str, data: TaskReconcileInput,
                            request: Request) -> dict[str, Any]:
        if step_id not in {"B", "D", "E"}:
            raise HTTPException(422, detail={"code": "STEP_NOT_RECONCILABLE"})
        current_id, store, task = require_task(request, task_id)
        step = task["steps"][step_id]
        if task["status"] != "needs_review" or step["status"] != "needs_review" or not step.get("operation_id"):
            raise HTTPException(409, detail={"code": "STEP_NOT_PENDING_REVIEW"})
        operation_id = step["operation_id"]
        receipt = store.transfer_receipt(current_id, operation_id)
        if step_id == "B":
            subscription_id = (receipt or {}).get("subscription_id")
            subscription = store.subscription(current_id, subscription_id) if subscription_id else None
            proved_success = bool(receipt and receipt.get("status") == "succeeded"
                                  and subscription and subscription["status"] == "cancelled")
            proved_failure = bool(receipt and receipt.get("status") == "failed"
                                  and subscription and subscription["status"] == "active")
            reconciled_by = "receipt_subscription_state"
        else:
            scenario = store.scenario_state(current_id)
            events = {item["source_entry_id"]: item for item in scenario["events"]} if scenario else {}
            accounts = store.accounts(current_id)
            debit = next((item["balance_cents"] for item in accounts if item["name"] == "借记卡6688"), None)
            card = next((item["balance_cents"] for item in accounts if item["name"] == "虚构信用卡4321"), None)
            source_ids = ("NOV002", "NOV003") if step_id == "D" else ("NOV004",)
            expected_success = (558400, 0) if step_id == "D" else (358400, 0)
            expected_failure = (842400, 284000) if step_id == "D" else (558400, 0)
            proved_success = (receipt and receipt["status"] == "succeeded"
                              and all(events.get(source_id, {}).get("status") == "applied"
                                      and events[source_id].get("operation_id") == operation_id
                                      for source_id in source_ids)
                              and (debit, card) == expected_success)
            proved_failure = (receipt and receipt["status"] == "failed"
                              and all(events.get(source_id, {}).get("status") == "not_applied"
                                      for source_id in source_ids)
                              and (debit, card) == expected_failure)
            reconciled_by = "receipt_ledger_balance"
        if proved_success:
            store.set_task_step(current_id, task_id, step_id, {"needs_review"}, "succeeded",
                                {"reconciled_by": reconciled_by, "receipt": receipt}, operation_id)
            return {"task_id": task_id, "step_id": step_id, "status": "succeeded", "receipt": receipt}
        if proved_failure and data.confirmed_reissue:
            store.record_task_event(current_id, task_id, step_id, "reconcile", "reissue_authorized",
                                    {"operation_id": operation_id, "confirmed_reissue": True}, operation_id)
            store.set_task_step(current_id, task_id, step_id, {"needs_review"}, "ready",
                                {"reissue_authorized_by": "user"})
            return {"task_id": task_id, "step_id": step_id, "status": "ready",
                    "message": ("已证实原操作失败；请重新发起本步骤并再次确认。"
                                if step_id == "B" else
                                "已证实原操作失败；请用新幂等键重新准备并重新授权。")}
        if proved_failure:
            return {"task_id": task_id, "step_id": step_id, "status": "failed_confirmed",
                    "message": "已证实失败；重新发起需要用户明确同意。"}
        store.record_task_event(current_id, task_id, step_id, "reconcile", "still_unknown",
                                {"operation_id": operation_id}, operation_id)
        raise HTTPException(409, detail={"code": "RESULT_STILL_UNKNOWN", "operation_id": operation_id})

    @application.post("/api/v1/operations/transfer/prepare", status_code=201)
    def prepare_transfer(data: TransferInput, request: Request) -> dict[str, Any]:
        contact_id = data.contact_id
        if data.slots is not None:
            slots = data.slots.model_dump(exclude_none=True)
            recipient = slots.get("recipient")
            amount_cents = slots.get("amount_cents")
            purpose = slots.get("purpose")
            contact_id = slots.get("contact_id", contact_id)
            if not recipient or amount_cents is None or not purpose or not purpose.strip():
                raise HTTPException(422, detail={"code": "CLARIFICATION_REQUIRED", "message": "请明确收款人、金额和用途。"})
        else:
            text = (data.text or "").strip()
            recipient_match = re.match(r"给(.+?)转\s*(.*)", text)
            if not recipient_match:
                raise HTTPException(422, detail={"code": "CLARIFICATION_REQUIRED", "message": "请明确收款人、金额和用途。"})
            recipient, remainder = recipient_match.groups()
            match = re.fullmatch(r"[¥￥]?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:元|块)?\s*(.*)", remainder)
            if not match:
                raise HTTPException(422, detail={"code": "CLARIFICATION_REQUIRED", "message": "请补充转账金额。"})
            amount_cents = int(Decimal(match.group(1).replace(",", "")) * 100)
            if amount_cents <= 0:
                raise HTTPException(422, detail={"code": "CLARIFICATION_REQUIRED", "message": "请提供大于零的转账金额。"})
            purpose = match.group(2).strip() or ("生活费" if recipient == "妈妈" else "")
        contact = None
        if recipient == "陈默":
            if not contact_id:
                missing_slots = []
                if data.slots is None:
                    candidate_amount = re.match(r"[¥￥]?\s*[\d,]+(?:\.\d{1,2})?\s*(.*)", remainder)
                    if not candidate_amount:
                        missing_slots.append("amount")
                    elif not candidate_amount.group(1):
                        missing_slots.append("purpose")
                return JSONResponse(status_code=200, content={"status": "clarification_required", "candidates": list(CONTACTS.values()),
                    "missing_slots": missing_slots, "demo": True})
            contact = CONTACTS.get(contact_id)
            if not contact:
                raise HTTPException(422, detail={"code": "INVALID_CONTACT_SELECTION"})
        elif recipient != "妈妈" or contact_id:
            raise HTTPException(422, detail={"code": "CLARIFICATION_REQUIRED", "message": "请从可识别的收款人中选择。"})
        current_id = session_id(request)
        if not purpose:
            raise HTTPException(422, detail={"code": "CLARIFICATION_REQUIRED", "message": "请补充转账用途。"})
        idempotency_key = data.idempotency_key or str(uuid4())
        summary = {"recipient": recipient, "amount_cents": amount_cents, "source_account": "借记卡6688",
                   "purpose": purpose, "execution_timing": "立即", "risk_level": "YELLOW", "demo": True}
        if contact:
            summary.update({key: contact[key] for key in ("contact_id", "relationship", "account_suffix")})
        store = get_store(request)
        is_frozen_transfer = recipient == "妈妈" and amount_cents == 200000 and purpose == "生活费"
        if is_frozen_transfer:
            ready, reason, receipt = store.scenario_event_ready_for_transfer(current_id)
            if reason == "ALREADY_APPLIED" and receipt:
                return {"operation_id": receipt["operation_id"], "receipt": receipt, "demo": True}
            if not ready:
                raise HTTPException(409, detail={"code": reason or "SCENARIO_EVENT_NOT_READY"})
        operation_id, existed, same_parameters, summary, summary_hash = store.create_transfer(current_id, summary, idempotency_key)
        if not operation_id:
            raise HTTPException(423, detail={"code": "SESSION_LOCKED", "message": "当前会话已锁定 15 分钟。"})
        if existed and not same_parameters:
            store.record_audit(current_id, operation_id, "transfer_prepare", "rejected",
                               error_code="IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PARAMETERS")
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PARAMETERS", "operation_id": operation_id})
        features = store.transfer_risk_features(current_id, recipient, amount_cents)
        risk_advice = score_risk(features)
        store.record_audit(current_id, operation_id, "risk_advice", "scored",
                           parameters={"features": features, "model_version": risk_advice["model_version"]})
        return {"operation_id": operation_id, "summary": summary, "summary_hash": summary_hash,
                "risk_advice": risk_advice}

    @application.post("/api/v1/operations/repayment/prepare", status_code=201)
    def prepare_repayment(
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key:
            raise HTTPException(422, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
        current_id = session_id(request)
        store = get_store(request)
        scenario = store.scenario_state(current_id)
        if not scenario:
            raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND"})
        states = {event["source_entry_id"]: event["status"] for event in scenario["events"]}
        if (scenario["scenario_date"] < "2026-11-05" or states.get("NOV001") != "applied"
                or states.get("NOV002") != "not_applied" or states.get("NOV003") != "not_applied"):
            raise HTTPException(409, detail={"code": "SCENARIO_EVENT_NOT_READY"})
        summary = {"recipient": "虚构信用卡4321", "amount_cents": 284000,
                   "source_account": "借记卡6688", "purpose": "归还10月虚构信用卡账单",
                   "execution_timing": "立即", "risk_level": "RED", "demo": True}
        operation_id, existed, same_parameters, summary, summary_hash = store.create_transfer(
            current_id, summary, idempotency_key, operation_type="repayment",
        )
        if not operation_id:
            raise HTTPException(423, detail={"code": "SESSION_LOCKED"})
        if existed and not same_parameters:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PARAMETERS"})
        return {"operation_id": operation_id, "summary": summary, "summary_hash": summary_hash}

    @application.post("/api/v1/operations/{operation_id}/verify")
    def verify_transfer(operation_id: str, data: VerificationInput, request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        if not get_store(request).task_operation_allowed(current_id, operation_id):
            raise HTTPException(409, detail={"code": "TASK_STEP_NOT_ACTIVE"})
        result = get_store(request).verify_transfer(current_id, operation_id, data.code)
        if result == "locked":
            raise HTTPException(423, detail={"code": "SESSION_LOCKED", "message": "当前会话已锁定 15 分钟。"})
        if result == "invalid":
            raise HTTPException(401, detail={"code": "INVALID_DEMO_CODE"})
        if result == "missing":
            raise HTTPException(404, detail={"code": "OPERATION_NOT_FOUND"})
        if result == "finished":
            raise HTTPException(409, detail={"code": "OPERATION_ALREADY_FINISHED"})
        return {"verified": True, "demo": True}

    @application.post("/api/v1/operations/{operation_id}/confirm")
    def confirm_transfer(operation_id: str, data: ConfirmationInput, request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        store = get_store(request)
        if not store.task_operation_allowed(current_id, operation_id):
            raise HTTPException(409, detail={"code": "TASK_STEP_NOT_ACTIVE"})
        result = store.confirm_transfer(current_id, operation_id, data.summary_hash)
        if result == "missing":
            raise HTTPException(404, detail={"code": "OPERATION_NOT_FOUND"})
        if result == "finished":
            raise HTTPException(409, detail={"code": "OPERATION_ALREADY_FINISHED"})
        if result != "confirmed":
            store.record_audit(current_id, operation_id, "transfer_confirm", "rejected",
                               confirmation={"confirmed": False}, error_code="CONFIRMATION_REQUIRED")
            locked = store.record_security_rejection(current_id, operation_id, "CONFIRMATION_REQUIRED")
            raise HTTPException(423 if locked else 409,
                                detail={"code": "SESSION_LOCKED" if locked else "CONFIRMATION_REQUIRED"})
        return {"confirmed": True, "demo": True}

    @application.post("/api/v1/operations/{operation_id}/execute")
    def execute_transfer(operation_id: str, request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        store = get_store(request)
        if not store.task_operation_allowed(current_id, operation_id):
            raise HTTPException(409, detail={"code": "TASK_STEP_NOT_ACTIVE"})
        binding = store.task_operation_binding(current_id, operation_id)
        if store.operation_type(current_id, operation_id) == "repayment":
            receipt, error = store.execute_repayment(current_id, operation_id)
            if error or not receipt:
                if binding and error == "EVENT_RESULT_UNKNOWN":
                    store.set_task_step(current_id, binding[0], binding[1],
                                        {"awaiting_authorization"}, "needs_review",
                                        {"reason": "EVENT_RESULT_UNKNOWN", "operation_id": operation_id}, operation_id)
                store.record_audit(current_id, operation_id, "repayment_execute", "rejected",
                                   error_code=error or "RED_REPAYMENT_AUTH_REQUIRED")
                if error == "RED_REPAYMENT_AUTH_REQUIRED":
                    locked = store.record_security_rejection(current_id, operation_id, error)
                    if locked:
                        raise HTTPException(423, detail={"code": "SESSION_LOCKED"})
                raise HTTPException(403, detail={"code": error or "RED_REPAYMENT_AUTH_REQUIRED"})
            if binding:
                task_status = ("succeeded" if task_receipt_matches_ledger(
                    store, current_id, binding[1], operation_id, receipt) else "needs_review")
                store.set_task_step(current_id, binding[0], binding[1],
                                    {"awaiting_authorization"}, task_status,
                                    {"receipt": receipt, "authorized_by": "visitor",
                                     "authorization_scope": "repayment_2840"}, operation_id)
            return {"receipt": receipt}
        receipt = store.execute_transfer(current_id, operation_id)
        if not receipt:
            store.record_audit(current_id, operation_id, "execute", "rejected",
                                error_code="VERIFICATION_AND_CONFIRMATION_REQUIRED")
            locked = store.record_security_rejection(current_id, operation_id,
                                                     "VERIFICATION_AND_CONFIRMATION_REQUIRED")
            raise HTTPException(423 if locked else 403,
                                detail={"code": "SESSION_LOCKED" if locked else "VERIFICATION_AND_CONFIRMATION_REQUIRED"})
        if binding:
            task_status = ("succeeded" if task_receipt_matches_ledger(
                store, current_id, binding[1], operation_id, receipt) else
                           "failed" if receipt["status"] == "failed" else "needs_review")
            store.set_task_step(current_id, binding[0], binding[1],
                                {"awaiting_authorization"}, task_status,
                                {"receipt": receipt, "authorized_by": "visitor",
                                 "authorization_scope": "transfer_mother_2000"}, operation_id)
        return {"receipt": receipt}

    @application.post("/api/v1/operations/{operation_id}/cancel")
    def cancel_transfer(operation_id: str, request: Request) -> dict[str, Any]:
        current_id = session_id(request)
        store = get_store(request)
        if store.task_operation_binding(current_id, operation_id):
            raise HTTPException(409, detail={"code": "USE_TASK_STEP_CANCEL"})
        result = store.cancel_transfer(current_id, operation_id)
        if result == "missing":
            raise HTTPException(404, detail={"code": "OPERATION_NOT_FOUND"})
        if result == "finished":
            store.record_audit(current_id, operation_id, "transfer_cancel", "rejected",
                               error_code="OPERATION_ALREADY_FINISHED")
            raise HTTPException(409, detail={"code": "OPERATION_ALREADY_FINISHED", "operation_id": operation_id})
        return {"status": "cancelled", "demo": True}

    @application.get("/api/v1/operations/latest")
    def get_latest_operation(request: Request) -> dict[str, Any]:
        receipt = get_store(request).latest_receipt(session_id(request))
        if not receipt:
            raise HTTPException(404, detail={"code": "OPERATION_NOT_FOUND"})
        return {"receipt": receipt}

    @application.get("/api/v1/operations/{operation_id}")
    def get_transfer(operation_id: str, request: Request) -> dict[str, Any]:
        receipt = get_store(request).transfer_receipt(session_id(request), operation_id)
        if not receipt:
            raise HTTPException(404, detail={"code": "OPERATION_NOT_FOUND"})
        return {"receipt": receipt}

    @application.get("/api/v1/operations/{operation_id}/audit")
    def get_operation_audit(operation_id: str, request: Request) -> dict[str, Any]:
        events = get_store(request).operation_audit(session_id(request), operation_id)
        if not events:
            raise HTTPException(404, detail={"code": "OPERATION_AUDIT_NOT_FOUND"})
        return {"operation_id": operation_id, "events": events, "demo": True}

    @application.get("/", response_class=HTMLResponse)
    @application.get("/accounts", response_class=HTMLResponse)
    @application.get("/bills", response_class=HTMLResponse)
    @application.get("/operations", response_class=HTMLResponse)
    @application.get("/results", response_class=HTMLResponse)
    @application.get("/subscriptions", response_class=HTMLResponse)
    def page(request: Request) -> HTMLResponse:
        titles = {"/": "模拟账户", "/accounts": "账户", "/bills": "账单", "/subscriptions": "订阅", "/operations": "会话", "/results": "操作状态"}
        return HTMLResponse(_page(titles[request.url.path]))

    return application


app = create_app()
