from __future__ import annotations

import json
import os
import re
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(pattern=r"^[A-Z][A-Z0-9]{0,7}$")
    action: Literal[
        "bill_query", "subscription_query", "account_query", "subscription_cancel",
        "wait_salary", "repayment", "transfer", "summary",
    ]
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    proposed_risk: Literal["GREEN", "YELLOW", "RED"]


class PlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["banking_task", "clarification_required", "unsupported"]
    slots: dict[str, str | int] = Field(default_factory=dict)
    missing_slots: list[str] = Field(default_factory=list, max_length=8)
    steps: list[PlanStep] = Field(default_factory=list, max_length=8)


def validate_plan(raw: Any) -> PlanDraft:
    draft = PlanDraft.model_validate(raw)
    ids = [step.step_id for step in draft.steps]
    if len(ids) != len(set(ids)):
        raise ValueError("DUPLICATE_STEP_ID")
    known: set[str] = set()
    for step in draft.steps:
        if step.step_id in step.depends_on or any(dependency not in known for dependency in step.depends_on):
            raise ValueError("INVALID_DAG_DEPENDENCY")
        known.add(step.step_id)
    if draft.intent != "banking_task" and draft.steps:
        raise ValueError("NON_EXECUTABLE_INTENT_HAS_STEPS")
    return draft


RISK_BY_ACTION = {
    "bill_query": ("GREEN", "只读取模拟账单"),
    "subscription_query": ("GREEN", "只读取模拟订阅"),
    "account_query": ("GREEN", "只读取模拟账户"),
    "wait_salary": ("GREEN", "只等待并核对工资，不推进模拟日期"),
    "summary": ("GREEN", "只汇总已核验状态"),
    "subscription_cancel": ("YELLOW", "改变订阅状态，需用户确认对象和影响"),
    "repayment": ("RED", "还款改变两本账，需独立模拟验证和同参数确认"),
    "transfer": ("RED", "草案阶段保守按红色展示；准备时按金额和当日累计重新判级"),
}


def review_plan(draft: PlanDraft, text: str) -> dict[str, Any]:
    reviewed = []
    for step in draft.steps:
        risk_level, reason = RISK_BY_ACTION[step.action]
        reviewed.append({"step_id": step.step_id, "action": step.action,
                         "proposed_risk": step.proposed_risk, "risk_level": risk_level,
                         "reason": reason, "authorization": "none"})
    issues = []
    server_missing: list[str] = []
    transfer_requested = "转" in text and not any(
        marker in text for marker in ("账单描述", "账单备注", "账单原文")
    )
    if transfer_requested:
        if not re.search(r"\d[\d,，]*\s*元", text) or any(
            marker in text for marker in ("金额没说", "金额我还没说", "金额未定", "金额等我确认")
        ):
            server_missing.append("amount_cents")
        if not re.search(r"给[\u4e00-\u9fa5]{1,12}转", text) or any(
            marker in text for marker in ("收款人等我确认", "收款人未定", "收款人还没说", "给谁还没定")
        ):
            server_missing.append("recipient")
    for slot in server_missing:
        if slot not in draft.missing_slots:
            issues.append({"code": "MODEL_MISSED_REQUIRED_SLOT", "slot": slot})
    if server_missing:
        issues.append({"code": "CLARIFICATION_REQUIRED", "slots": server_missing})
    if any(re.search(r"balance|receipt|authoriz|verified|confirmed", key, re.I)
           for key in draft.slots):
        issues.append({"code": "UNTRUSTED_RESULT_OR_AUTHORIZATION_SLOT"})
    if all(term in text for term in ("云盘", "工资", "还清信用卡", "妈妈", "还剩多少")):
        actions = {step.action for step in draft.steps}
        required = set(RISK_BY_ACTION)
        missing = sorted(required - actions)
        if missing:
            issues.append({"code": "MISSING_REQUIRED_STEPS", "actions": missing})
        by_action = {step.action: step for step in draft.steps}
        for child, parent in (("subscription_cancel", "subscription_query"),
                              ("repayment", "wait_salary"), ("transfer", "repayment")):
            if child in by_action and parent in by_action and by_action[parent].step_id not in by_action[child].depends_on:
                issues.append({"code": "MISSING_DEPENDENCY", "child": child, "parent": parent})
        if "summary" in by_action:
            summary_deps = set(by_action["summary"].depends_on)
            for parent in ("subscription_cancel", "repayment", "transfer"):
                if parent in by_action and by_action[parent].step_id not in summary_deps:
                    issues.append({"code": "MISSING_SUMMARY_DEPENDENCY", "parent": parent})
    return {"steps": reviewed, "issues": issues, "server_missing_slots": server_missing,
            "accepted": draft.intent == "banking_task" and not issues}


class ModelUnavailable(Exception):
    pass


class DeepSeekPlanner:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")

    def draft(self, text: str) -> PlanDraft:
        if not self.api_key:
            raise ModelUnavailable("MODEL_KEY_NOT_CONFIGURED")
        system_prompt = (
            "只输出一个 JSON 对象，严格使用 intent、slots、missing_slots、steps 四个顶层字段，不加其他字段。"
            "intent 只能为 banking_task、clarification_required、unsupported。"
            "steps 中每项必须恰有 step_id、action、depends_on、proposed_risk 四字段。"
            "action 只能为 bill_query、subscription_query、account_query、subscription_cancel、"
            "wait_salary、repayment、transfer、summary；proposed_risk 只能为 GREEN、YELLOW、RED。"
            "step_id 用 A、B、C 等大写字母，steps 按依赖拓扑顺序排列。"
            "你只产草案，不调用工具，不声称已知道余额、回执或授权。"
            "不明确的金额、收款人、日期列入 missing_slots，不得猜测；即使 steps 为空也必须列出缺槽。"
            "例如‘给妈妈转一笔，金额还没说’输出 intent=clarification_required、"
            "missing_slots=[\"amount_cents\"]、steps=[]；"
            "‘转2000元，收款人等我确认’输出 intent=clarification_required、"
            "missing_slots=[\"recipient\"]、steps=[]。"
            "如果要求最后剩余金额，必须包含 account_query 且汇总依赖资金步骤；"
            "偿还信用卡与超过 1000 元的转账提议 RED，不把用户一句话当作验证或确认。"
            "初始查询可并行；若用户说先还款再转账，转账必须依赖还款；"
            "最终汇总必须依赖取消订阅、还款和转账的最终状态。"
            "查询账单的 JSON 示例：{\"intent\":\"banking_task\",\"slots\":{\"month\":\"2026-10\"},"
            "\"missing_slots\":[],\"steps\":[{\"step_id\":\"A\",\"action\":\"bill_query\","
            "\"depends_on\":[],\"proposed_risk\":\"GREEN\"}]}。"
        )
        payload = json.dumps({
            "model": "deepseek-flash",
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": 1200,
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            "https://api.deepseek.com/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                result = json.load(response)
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop" or not choice["message"].get("content"):
                raise ModelUnavailable("MODEL_OUTPUT_INCOMPLETE")
            return validate_plan(json.loads(choice["message"]["content"]))
        except (HTTPError, URLError, TimeoutError):
            raise ModelUnavailable("MODEL_SERVICE_UNAVAILABLE") from None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError, ValueError):
            raise ModelUnavailable("MODEL_OUTPUT_INVALID") from None
