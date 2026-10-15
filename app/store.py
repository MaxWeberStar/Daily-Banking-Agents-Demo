from __future__ import annotations

import csv
import hashlib
import hmac
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4


SCENARIO_EVENT_IDS = ("NOV001", "NOV002", "NOV003", "NOV004", "NOV005")
SCENARIO_EVENT_GROUPS = {
    "NOV001": ("NOV001",),
    "NOV002": ("NOV002", "NOV003"),
    "NOV003": ("NOV002", "NOV003"),
    "NOV005": ("NOV005",),
}


def _matches_frozen_transfer(transaction: Any, amount_cents: int, recipient: str, purpose: str) -> bool:
    return (
        transaction["amount_cents"] == amount_cents
        and transaction["direction"] == "debit"
        and transaction["allocation"] == "母亲"
        and transaction["account"] == "借记卡6688"
        and transaction["account_type"] == "deposit"
        and transaction["entry_type"] == "transfer"
        and str(transaction["posting_date"])[:10] == "2026-11-05"
        and str(transaction["posting_time"])[:5] == "09:10"
        and transaction["payment_channel"] == "模拟转账"
        and recipient == "妈妈"
        and purpose == "生活费"
    )


class Store:
    def __init__(self, database_url: str, ledger_path: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.database_url = database_url
        self.ledger_path = ledger_path
        self.clock = clock or (lambda: datetime.now(UTC))
        self.is_sqlite = database_url.startswith("sqlite:")
        if self.is_sqlite:
            # SQLite is only injected by isolated tests; production uses Postgres.
            self.database_path = Path(database_url.removeprefix("sqlite:///"))
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_sqlite()

    @contextmanager
    def _connection(self, session_id: str | None = None) -> Iterator[Any]:
        if self.is_sqlite:
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()
            return

        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.database_url, row_factory=dict_row, prepare_threshold=None) as connection:
            if session_id:
                connection.execute("SELECT set_config('app.session_id', %s, true)", (str(session_id),))
            yield connection

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if not self.is_sqlite else statement

    def check_ready(self) -> None:
        with self._connection() as connection:
            connection.execute("SELECT 1")

    def _write_audit(self, connection: Any, session_id: str, operation_id: str, action: str, status: str,
                     parameters: dict[str, Any] | None = None, confirmation: dict[str, Any] | None = None,
                     receipt: dict[str, Any] | None = None, error_code: str | None = None) -> None:
        values = [json.dumps(value, ensure_ascii=False) if value is not None else None
                  for value in (parameters, confirmation, receipt)]
        json_slots = "?, ?, ?" if self.is_sqlite else "CAST(? AS jsonb), CAST(? AS jsonb), CAST(? AS jsonb)"
        connection.execute(self._sql(
            "INSERT INTO operation_audit (operation_id, session_id, action, status, parameters, confirmation, receipt, occurred_at, error_code) "
            f"VALUES (?, ?, ?, ?, {json_slots}, ?, ?)"),
            (operation_id, session_id, action, status, values[0] or "{}", values[1], values[2],
             self.clock().isoformat(), error_code))
        bound = connection.execute(self._sql(
            "SELECT task_id, step_id FROM operations WHERE session_id = ? AND id = ?"),
            (session_id, operation_id),
        ).fetchone()
        task_id = bound["task_id"] if bound else None
        step_id = bound["step_id"] if bound else None
        if not task_id and action in {"security_rejection", "circuit_breaker", "security_unlock", "locked_request"}:
            active = connection.execute(self._sql(
                "SELECT id FROM task_chains WHERE session_id = ? AND status IN ('active', 'paused', 'needs_review') "
                "ORDER BY created_at DESC LIMIT 1"),
                (session_id,),
            ).fetchone()
            task_id = active["id"] if active else None
        if task_id:
            details = {"parameters": parameters or {}, "confirmation": confirmation,
                       "receipt": receipt, "error_code": error_code}
            json_slot = "?" if self.is_sqlite else "CAST(? AS jsonb)"
            connection.execute(self._sql(
                "INSERT INTO task_events (task_id, session_id, step_id, operation_id, action, status, details, occurred_at) "
                f"VALUES (?, ?, ?, ?, ?, ?, {json_slot}, ?)"),
                (task_id, session_id, step_id, operation_id, action, status,
                 json.dumps(details, ensure_ascii=False), self.clock().isoformat()),
            )

    def record_audit(self, session_id: str, operation_id: str, action: str, status: str,
                     parameters: dict[str, Any] | None = None, confirmation: dict[str, Any] | None = None,
                     receipt: dict[str, Any] | None = None, error_code: str | None = None) -> None:
        with self._connection(session_id) as connection:
            self._write_audit(connection, session_id, operation_id, action, status, parameters,
                              confirmation, receipt, error_code)

    def _write_task_event(self, connection: Any, session_id: str, task_id: str,
                          step_id: str | None, action: str, status: str,
                          details: dict[str, Any] | None = None,
                          operation_id: str | None = None) -> None:
        json_slot = "?" if self.is_sqlite else "CAST(? AS jsonb)"
        connection.execute(self._sql(
            "INSERT INTO task_events (task_id, session_id, step_id, operation_id, action, status, details, occurred_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, {json_slot}, ?)"),
            (task_id, session_id, step_id, operation_id, action, status,
             json.dumps(details or {}, ensure_ascii=False), self.clock().isoformat()),
        )

    def create_task_chain(self, session_id: str, plan: dict[str, Any],
                          initial_evidence: dict[str, Any]) -> dict[str, Any] | None:
        task_id = str(uuid4())
        now = self.clock().isoformat()
        steps: dict[str, dict[str, Any]] = {
            "A": {"action": "parallel_queries", "depends_on": [], "risk_level": "GREEN",
                  "risk_reason": "账单、订阅、账户三项只读并行查询", "status": "succeeded", "evidence": initial_evidence},
            "B": {"action": "subscription_cancel", "depends_on": ["A"], "risk_level": "YELLOW",
                  "risk_reason": "取消订阅改变状态，需用户明确确认对象和影响", "status": "ready", "operation_id": None},
            "C": {"action": "wait_salary", "depends_on": [], "risk_level": "GREEN",
                  "risk_reason": "只核对工资事件，Agent 不推进模拟日期", "status": "waiting"},
            "D": {"action": "repayment", "depends_on": ["C"], "risk_level": "RED",
                  "risk_reason": "还款 ¥2,840，需独立模拟验证和同参确认", "status": "waiting", "operation_id": None},
            "E": {"action": "transfer", "depends_on": ["D"], "risk_level": "RED",
                  "risk_reason": "转账 ¥2,000，需重新模拟验证和同参确认", "status": "waiting", "operation_id": None},
            "F": {"action": "summary", "depends_on": ["B", "D", "E"], "risk_level": "GREEN",
                  "risk_reason": "只依据回执、流水和账户汇总", "status": "waiting"},
        }
        with self._connection(session_id) as connection:
            if self.is_sqlite:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute(self._sql("SELECT id FROM sessions WHERE id = ? FOR UPDATE"), (session_id,))
            existing = connection.execute(self._sql(
                "SELECT id FROM task_chains WHERE session_id = ? AND status IN ('active', 'paused', 'needs_review')"),
                (session_id,),
            ).fetchone()
            if existing:
                return None
            json_slots = "?, ?" if self.is_sqlite else "CAST(? AS jsonb), CAST(? AS jsonb)"
            connection.execute(self._sql(
                "INSERT INTO task_chains (id, session_id, status, plan, step_state, created_at, updated_at) "
                f"VALUES (?, ?, 'active', {json_slots}, ?, ?)"),
                (task_id, session_id, json.dumps(plan, ensure_ascii=False),
                 json.dumps(steps, ensure_ascii=False), now, now),
            )
            self._write_task_event(connection, session_id, task_id, None, "intent_draft", "proposed",
                                   {"mode": "online_deepseek", "plan": plan})
            self._write_task_event(connection, session_id, task_id, None, "plan_review", "accepted",
                                   {"steps": list(steps)})
            self._write_task_event(connection, session_id, task_id, "A", "read_tools", "succeeded",
                                   initial_evidence)
        return {"task_id": task_id, "status": "active", "steps": steps, "plan": plan}

    def task_chain(self, session_id: str, task_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT id, status, plan, step_state, created_at, updated_at "
                "FROM task_chains WHERE session_id = ? AND id = ?"),
                (session_id, task_id),
            ).fetchone()
        if not row:
            return None
        return {"task_id": row["id"], "status": row["status"],
                "plan": row["plan"] if isinstance(row["plan"], dict) else json.loads(row["plan"]),
                "steps": row["step_state"] if isinstance(row["step_state"], dict) else json.loads(row["step_state"]),
                "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"])}

    def active_task_id(self, session_id: str) -> str | None:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT id FROM task_chains WHERE session_id = ? "
                "AND status IN ('active', 'paused', 'needs_review') ORDER BY created_at DESC LIMIT 1"),
                (session_id,),
            ).fetchone()
        return row["id"] if row else None

    def latest_task_id(self, session_id: str) -> str | None:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT id FROM task_chains WHERE session_id = ? ORDER BY created_at DESC LIMIT 1"),
                (session_id,),
            ).fetchone()
        return row["id"] if row else None

    def task_events(self, session_id: str, task_id: str) -> list[dict[str, Any]]:
        with self._connection(session_id) as connection:
            rows = connection.execute(self._sql(
                "SELECT step_id, operation_id, action, status, details, occurred_at "
                "FROM task_events WHERE session_id = ? AND task_id = ? ORDER BY id"),
                (session_id, task_id),
            ).fetchall()
        return [{"step_id": row["step_id"], "operation_id": row["operation_id"],
                 "action": row["action"], "status": row["status"],
                 "details": row["details"] if isinstance(row["details"], dict) else json.loads(row["details"]),
                 "occurred_at": str(row["occurred_at"])} for row in rows]

    def record_task_event(self, session_id: str, task_id: str, step_id: str | None,
                          action: str, status: str, details: dict[str, Any] | None = None,
                          operation_id: str | None = None) -> None:
        with self._connection(session_id) as connection:
            self._write_task_event(connection, session_id, task_id, step_id, action, status,
                                   details, operation_id)

    def task_operation_binding(self, session_id: str, operation_id: str) -> tuple[str, str] | None:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT task_id, step_id FROM operations WHERE session_id = ? AND id = ?"),
                (session_id, operation_id),
            ).fetchone()
        return (row["task_id"], row["step_id"]) if row and row["task_id"] and row["step_id"] else None

    def set_task_status(self, session_id: str, task_id: str, status: str) -> bool:
        with self._connection(session_id) as connection:
            current = connection.execute(self._sql(
                "SELECT status FROM task_chains WHERE session_id = ? AND id = ?" +
                (" FOR UPDATE" if not self.is_sqlite else "")),
                (session_id, task_id),
            ).fetchone()
            if not current or current["status"] not in {"active", "paused", "needs_review"}:
                return False
            connection.execute(self._sql(
                "UPDATE task_chains SET status = ?, updated_at = ? WHERE session_id = ? AND id = ?"),
                (status, self.clock().isoformat(), session_id, task_id),
            )
            if status == "paused":
                pending = connection.execute(self._sql(
                    "SELECT id FROM operations WHERE session_id = ? AND task_id = ? AND status = 'pending' "
                    "AND (verified = TRUE OR confirmed = TRUE)"),
                    (session_id, task_id),
                ).fetchall()
                connection.execute(self._sql(
                    "UPDATE operations SET verified = FALSE, confirmed = FALSE "
                    "WHERE session_id = ? AND task_id = ? AND status = 'pending'"),
                    (session_id, task_id),
                )
                for operation in pending:
                    self._write_audit(connection, session_id, operation["id"],
                                      "authorization_invalidated", "succeeded",
                                      parameters={"reason": "task_paused"})
            self._write_task_event(connection, session_id, task_id, None, "task_status", status)
        return True

    def set_task_step(self, session_id: str, task_id: str, step_id: str,
                      expected: set[str], status: str, details: dict[str, Any] | None = None,
                      operation_id: str | None = None) -> bool:
        with self._connection(session_id) as connection:
            if self.is_sqlite:
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(self._sql(
                "SELECT status, step_state FROM task_chains WHERE session_id = ? AND id = ?" +
                (" FOR UPDATE" if not self.is_sqlite else "")),
                (session_id, task_id),
            ).fetchone()
            if not row or (row["status"] != "active"
                           and not (row["status"] == "paused" and status == "cancelled")
                           and not (row["status"] == "needs_review" and "needs_review" in expected
                                    and status in {"ready", "succeeded", "failed"})):
                return False
            steps = row["step_state"] if isinstance(row["step_state"], dict) else json.loads(row["step_state"])
            if step_id not in steps or steps[step_id]["status"] not in expected:
                return False
            steps[step_id]["status"] = status
            if status == "ready" and "needs_review" in expected:
                steps[step_id]["operation_id"] = None
                steps[step_id].pop("details", None)
            if operation_id:
                steps[step_id]["operation_id"] = operation_id
            if details:
                steps[step_id]["details"] = details
            if step_id == "C" and status == "succeeded":
                steps["D"]["status"] = "ready"
            if step_id == "D" and status == "succeeded":
                steps["E"]["status"] = "ready"
            if step_id == "D" and status == "ready":
                steps["E"]["status"] = "waiting"
            if step_id == "D" and status in {"failed", "cancelled"}:
                steps["E"]["status"] = "blocked"
            if status == "needs_review":
                for dependent in (("D", "E", "F") if step_id == "C" else
                                  ("E", "F") if step_id == "D" else ("F",)):
                    steps[dependent]["status"] = "frozen"
            elif row["status"] == "needs_review":
                if step_id == "B":
                    steps["F"]["status"] = "waiting"
                elif step_id == "D":
                    steps["F"]["status"] = "waiting"
                elif step_id == "E":
                    steps["F"]["status"] = "waiting"
            if (step_id != "F" and steps["B"]["status"] in {"succeeded", "failed", "cancelled"}
                    and steps["D"]["status"] in {"succeeded", "failed", "cancelled"}
                    and steps["E"]["status"] in {"succeeded", "failed", "cancelled", "blocked"}):
                steps["F"]["status"] = "ready"
            json_slot = "?" if self.is_sqlite else "CAST(? AS jsonb)"
            connection.execute(self._sql(
                f"UPDATE task_chains SET step_state = {json_slot}, status = ?, updated_at = ? WHERE session_id = ? AND id = ?"),
                (json.dumps(steps, ensure_ascii=False),
                 "needs_review" if status == "needs_review" else "active" if row["status"] == "needs_review" else row["status"],
                 self.clock().isoformat(), session_id, task_id),
            )
            self._write_task_event(connection, session_id, task_id, step_id, "step_state", status,
                                   details, operation_id)
        return True

    def task_operation_allowed(self, session_id: str, operation_id: str) -> bool:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT task_id, step_id FROM operations WHERE session_id = ? AND id = ?"),
                (session_id, operation_id),
            ).fetchone()
        if not row or not row["task_id"]:
            return True
        task = self.task_chain(session_id, row["task_id"])
        return bool(task and task["status"] == "active" and
                    task["steps"][row["step_id"]]["status"] == "awaiting_authorization")

    def session_locked(self, session_id: str) -> bool:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT failed_codes, locked_until FROM visitor_security WHERE session_id = ?"),
                (session_id,),
            ).fetchone()
            if not row or not row["locked_until"]:
                return False
            if self._datetime(row["locked_until"]) > self.clock():
                return True
            connection.execute(self._sql(
                "UPDATE visitor_security SET failed_codes = 0, locked_until = NULL WHERE session_id = ?"),
                (session_id,),
            )
            connection.execute(self._sql("DELETE FROM security_rejections WHERE session_id = ?"), (session_id,))
            self._write_audit(connection, session_id, str(uuid4()), "security_unlock", "succeeded",
                              parameters={"reason": "lock_expired"})
        return False

    def record_security_rejection(self, session_id: str, operation_id: str, error_code: str) -> bool:
        with self._connection(session_id) as connection:
            if not self.is_sqlite:
                connection.execute(self._sql("SELECT id FROM sessions WHERE id = ? FOR UPDATE"), (session_id,))
            self._write_audit(connection, session_id, operation_id, "security_rejection", "rejected",
                              error_code=error_code)
            connection.execute(self._sql(
                "INSERT INTO security_rejections (session_id, occurred_at) VALUES (?, ?)"),
                (session_id, self.clock().isoformat()),
            )
            rows = connection.execute(self._sql(
                "SELECT occurred_at FROM security_rejections WHERE session_id = ? "
                "ORDER BY id DESC LIMIT 3"),
                (session_id,),
            ).fetchall()
            if len(rows) < 3 or self._datetime(rows[-1]["occurred_at"]) < self.clock() - timedelta(minutes=10):
                return False
            locked_until = self.clock() + timedelta(minutes=15)
            connection.execute(self._sql(
                "INSERT INTO visitor_security (session_id, failed_codes, locked_until) VALUES (?, 0, ?) "
                "ON CONFLICT (session_id) DO UPDATE SET locked_until = excluded.locked_until"),
                (session_id, locked_until.isoformat()),
            )
            connection.execute(self._sql(
                "UPDATE operations SET verified = FALSE, confirmed = FALSE WHERE session_id = ? AND status = 'pending'"),
                (session_id,),
            )
            connection.execute(self._sql("DELETE FROM security_rejections WHERE session_id = ?"), (session_id,))
            self._write_audit(connection, session_id, operation_id, "circuit_breaker", "locked",
                              parameters={"reason": "three_unauthorized_attempts", "locked_until": locked_until.isoformat()})
        return True

    def operation_audit(self, session_id: str, operation_id: str) -> list[dict[str, Any]]:
        with self._connection(session_id) as connection:
            rows = connection.execute(self._sql(
                "SELECT action, status, parameters, confirmation, receipt, occurred_at, error_code "
                "FROM operation_audit WHERE session_id = ? AND operation_id = ? ORDER BY id"),
                (session_id, operation_id)).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            for key in ("parameters", "confirmation", "receipt"):
                if event[key] is not None and not isinstance(event[key], dict):
                    event[key] = json.loads(event[key])
            events.append(event)
        return events

    def _initialize_sqlite(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, cookie_hash TEXT UNIQUE NOT NULL,
                    reset_generation INTEGER NOT NULL DEFAULT 0,
                    scenario_date TEXT NOT NULL DEFAULT '2026-10-31',
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT NOT NULL, session_id TEXT NOT NULL, name TEXT NOT NULL,
                    account_type TEXT NOT NULL, balance_cents INTEGER NOT NULL,
                    PRIMARY KEY (session_id, id)
                );
                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT NOT NULL, source_entry_id TEXT NOT NULL, session_id TEXT NOT NULL, ledger_order INTEGER NOT NULL,
                    posting_date TEXT NOT NULL, posting_time TEXT NOT NULL, account TEXT NOT NULL,
                    account_type TEXT NOT NULL, direction TEXT NOT NULL, amount_cents INTEGER NOT NULL,
                    description TEXT NOT NULL, prd_category TEXT NOT NULL, initial_category TEXT NOT NULL,
                    allocation TEXT NOT NULL, payment_channel TEXT NOT NULL, bank_balance_after_cents INTEGER,
                    notes TEXT NOT NULL, entry_type TEXT NOT NULL, gross_amount_cents INTEGER NOT NULL,
                    include_in_monthly_spend INTEGER NOT NULL, card_balance_after_cents INTEGER,
                    PRIMARY KEY (session_id, id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS transactions_session_source_entry_id
                    ON transactions (session_id, source_entry_id);
                CREATE TABLE IF NOT EXISTS bill_category_corrections (
                    session_id TEXT NOT NULL, source_entry_id TEXT NOT NULL,
                    original_category TEXT NOT NULL, new_category TEXT NOT NULL, corrected_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, source_entry_id)
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT NOT NULL, source_id TEXT NOT NULL, session_id TEXT NOT NULL, merchant TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL, period TEXT NOT NULL, consecutive_charges INTEGER NOT NULL,
                    last_used_date TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY (session_id, id)
                );
                CREATE TABLE IF NOT EXISTS subscription_cancellations (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, subscription_id TEXT NOT NULL,
                    status TEXT NOT NULL, completed_at TEXT NOT NULL, receipt TEXT NOT NULL,
                    UNIQUE (session_id, subscription_id)
                );
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT NOT NULL, session_id TEXT NOT NULL, recipient TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL, purpose TEXT NOT NULL, summary_hash TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0, confirmed INTEGER NOT NULL DEFAULT 0,
                    requires_verification INTEGER NOT NULL DEFAULT 1,
                    receipt TEXT, status TEXT NOT NULL DEFAULT 'pending', operation_type TEXT NOT NULL DEFAULT 'transfer',
                    created_at TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    task_id TEXT, step_id TEXT,
                    UNIQUE (session_id, idempotency_key), PRIMARY KEY (session_id, id)
                );
                CREATE TABLE IF NOT EXISTS task_chains (
                    id TEXT NOT NULL, session_id TEXT NOT NULL, status TEXT NOT NULL,
                    plan TEXT NOT NULL, step_state TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, id)
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                    session_id TEXT NOT NULL, step_id TEXT, operation_id TEXT,
                    action TEXT NOT NULL, status TEXT NOT NULL,
                    details TEXT NOT NULL, occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scenario_event_applications (
                    session_id TEXT NOT NULL, source_entry_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('not_applied', 'applied')),
                    operation_id TEXT, applied_at TEXT,
                    PRIMARY KEY (session_id, source_entry_id),
                    FOREIGN KEY (session_id, operation_id) REFERENCES operations(session_id, id) ON DELETE CASCADE,
                    CHECK ((status = 'not_applied' AND operation_id IS NULL AND applied_at IS NULL)
                        OR (status = 'applied' AND operation_id IS NOT NULL AND applied_at IS NOT NULL))
                );
                CREATE TABLE IF NOT EXISTS operation_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL,
                    session_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
                    parameters TEXT NOT NULL, confirmation TEXT, receipt TEXT,
                    occurred_at TEXT NOT NULL, error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS visitor_security (
                    session_id TEXT PRIMARY KEY, failed_codes INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT
                );
                CREATE TABLE IF NOT EXISTS security_rejections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS intent_contexts (
                    session_id TEXT PRIMARY KEY, intent TEXT NOT NULL, slots TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reset_audit (
                    session_id TEXT NOT NULL, reset_generation INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY (session_id, reset_generation)
                );
                """
            )

    def create_session(self, cookie: str) -> tuple[str, datetime]:
        session_id = str(uuid4())
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=24)
        cookie_hash = hashlib.sha256(cookie.encode()).hexdigest()
        with self._connection(session_id) as connection:
            connection.execute(
                self._sql("INSERT INTO sessions (id, cookie_hash, created_at, expires_at) VALUES (?, ?, ?, ?)"),
                (session_id, cookie_hash, now, expires_at),
            )
            self._seed_session(connection, session_id)
        return session_id, expires_at

    def _seed_session(self, connection: Any, session_id: str) -> None:
        rows = self._ledger_rows()
        initial_rows = [row for row in rows if row["entry_id"] not in SCENARIO_EVENT_IDS]
        balances: dict[str, int] = {}
        account_types: dict[str, str] = {}
        for row in initial_rows:
            name = row["account"]
            account_types[name] = row["account_type"]
            balance_column = "card_balance_after_cny" if row["account_type"] == "credit_card" else "bank_balance_after_cny"
            if row[balance_column]:
                balances[name] = self._cents(row[balance_column])
        for name, balance in balances.items():
            connection.execute(
                self._sql("INSERT INTO accounts (id, session_id, name, account_type, balance_cents) VALUES (?, ?, ?, ?, ?)"),
                (str(uuid4()), session_id, name, account_types[name], balance),
            )
        for ledger_order, row in enumerate(initial_rows):
            self._insert_transaction(connection, session_id, row, ledger_order)
        present_event_ids = {row["entry_id"] for row in rows}
        for source_entry_id in SCENARIO_EVENT_IDS:
            if source_entry_id in present_event_ids:
                connection.execute(self._sql(
                    "INSERT INTO scenario_event_applications (session_id, source_entry_id, status) "
                    "VALUES (?, ?, 'not_applied')"),
                    (session_id, source_entry_id),
                )
        connection.execute(
            self._sql("INSERT INTO subscriptions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"),
            (str(uuid4()), "SUB-CLOUD", session_id, "云盘", 3000, "monthly", 7, date(2026, 7, 11), "active"),
        )
        # The schema requires usage-history values; they are masked for non-cloud subscriptions.
        connection.execute(
            self._sql("INSERT INTO subscriptions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"),
            (str(uuid4()), "SUB-KEEP", session_id, "Keep", 1900, "monthly", 0, date(1970, 1, 1), "active"),
        )

    def reset_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            if self.is_sqlite:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute(self._sql("SELECT id FROM sessions WHERE id = ? FOR UPDATE"), (session_id,))
            session = connection.execute(self._sql(
                "SELECT reset_generation FROM sessions WHERE id = ?"), (session_id,)).fetchone()
            if not session:
                return None
            generation = session["reset_generation"] + 1
            for table in ("operation_audit", "reset_audit", "task_events", "task_chains", "scenario_event_applications", "subscription_cancellations", "bill_category_corrections",
                          "operations", "intent_contexts", "accounts", "transactions", "subscriptions"):
                connection.execute(self._sql(f"DELETE FROM {table} WHERE session_id = ?"), (session_id,))
            connection.execute(self._sql(
                "UPDATE sessions SET scenario_date = ? WHERE id = ?"), ("2026-10-31", session_id))
            self._seed_session(connection, session_id)
            occurred_at = self.clock().isoformat()
            connection.execute(self._sql(
                "UPDATE sessions SET reset_generation = ? WHERE id = ?"), (generation, session_id))
            connection.execute(self._sql(
                "INSERT INTO reset_audit (session_id, reset_generation, occurred_at, result) VALUES (?, ?, ?, ?)"),
                (session_id, generation, occurred_at, "succeeded"))
        return {"reset_generation": generation, "occurred_at": occurred_at, "result": "succeeded"}

    def _insert_transaction(self, connection: Any, session_id: str, row: dict[str, str], ledger_order: int) -> str:
        transaction_id = str(uuid4())
        connection.execute(
            self._sql("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
            (
                transaction_id, row["entry_id"], session_id, ledger_order,
                row["posting_date"], row["posting_time"], row["account"], row["account_type"],
                row["direction"], self._cents(row["amount_cny"]), row["description"],
                row["prd_category"], row["initial_category"], row["allocation"], row["payment_channel"],
                self._optional_cents(row["bank_balance_after_cny"]), row["notes"], row["entry_type"],
                self._cents(row["gross_amount_cny"]), row["include_in_monthly_spend"] == "true",
                self._optional_cents(row["card_balance_after_cny"]),
            ),
        )
        return transaction_id

    def _scenario_event_rows(self) -> dict[str, dict[str, str]]:
        return {row["entry_id"]: row for row in self._ledger_rows() if row["entry_id"] in SCENARIO_EVENT_IDS}

    def scenario_state(self, session_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            session = connection.execute(self._sql(
                "SELECT scenario_date FROM sessions WHERE id = ?"), (session_id,)).fetchone()
            if not session:
                return None
            states = {
                row["source_entry_id"]: row
                for row in connection.execute(self._sql(
                    "SELECT source_entry_id, status, operation_id, applied_at "
                    "FROM scenario_event_applications WHERE session_id = ?"), (session_id,)).fetchall()
            }
        scenario_date = session["scenario_date"]
        scenario_date = scenario_date.isoformat() if not isinstance(scenario_date, str) else scenario_date
        events = []
        for source_entry_id, source in self._scenario_event_rows().items():
            state = states.get(source_entry_id)
            events.append({
                "source_entry_id": source_entry_id,
                "posting_date": source["posting_date"],
                "description": source["description"],
                "amount_cents": self._cents(source["amount_cny"]),
                "status": state["status"] if state else "not_applied",
                "operation_id": state["operation_id"] if state else None,
                "applied_at": state["applied_at"] if state else None,
            })
        return {"scenario_date": scenario_date, "events": events, "demo": True}

    def advance_scenario(self, session_id: str) -> tuple[dict[str, Any] | None, str | None]:
        with self._connection(session_id) as connection:
            if self.is_sqlite:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute(self._sql("SELECT id FROM sessions WHERE id = ? FOR UPDATE"), (session_id,))
            session = connection.execute(self._sql(
                "SELECT scenario_date FROM sessions WHERE id = ?"), (session_id,)).fetchone()
            if not session:
                return None, "SESSION_NOT_FOUND"
            security = connection.execute(self._sql(
                "SELECT locked_until FROM visitor_security WHERE session_id = ?"),
                (session_id,),
            ).fetchone()
            if security and security["locked_until"] and self._datetime(security["locked_until"]) > self.clock():
                return None, "SESSION_LOCKED"
            current_date = session["scenario_date"]
            current_date = current_date.isoformat() if not isinstance(current_date, str) else current_date
            if current_date == "2026-10-31":
                next_date = "2026-11-05"
            elif current_date == "2026-11-05":
                applied = {
                    row["source_entry_id"]
                    for row in connection.execute(self._sql(
                        "SELECT source_entry_id FROM scenario_event_applications "
                        "WHERE session_id = ? AND status = 'applied'"), (session_id,)).fetchall()
                }
                if not {"NOV001", "NOV002", "NOV003", "NOV004"}.issubset(applied):
                    return None, "SCENARIO_EVENTS_INCOMPLETE"
                next_date = "2026-11-10"
            else:
                return None, "SCENARIO_COMPLETE"
            connection.execute(self._sql(
                "UPDATE sessions SET scenario_date = ? WHERE id = ?"), (next_date, session_id))
        return self.scenario_state(session_id), None

    def scenario_event_ready_for_transfer(
        self, session_id: str
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        with self._connection(session_id) as connection:
            session = connection.execute(self._sql(
                "SELECT scenario_date FROM sessions WHERE id = ?"), (session_id,)).fetchone()
            if not session:
                return False, "SESSION_NOT_FOUND", None
            scenario_date = session["scenario_date"]
            scenario_date = scenario_date.isoformat() if not isinstance(scenario_date, str) else scenario_date
            states = {
                row["source_entry_id"]: row
                for row in connection.execute(self._sql(
                    "SELECT source_entry_id, status, operation_id FROM scenario_event_applications "
                    "WHERE session_id = ? AND source_entry_id IN ('NOV001', 'NOV002', 'NOV003', 'NOV004')"),
                    (session_id,),
                ).fetchall()
            }
            event = states.get("NOV004")
            if event and event["status"] == "applied" and event["operation_id"]:
                operation = connection.execute(self._sql(
                    "SELECT receipt FROM operations WHERE session_id = ? AND id = ?"),
                    (session_id, event["operation_id"]),
                ).fetchone()
                if operation and operation["receipt"]:
                    receipt = operation["receipt"] if isinstance(operation["receipt"], dict) else json.loads(operation["receipt"])
                    return True, "ALREADY_APPLIED", receipt
        if scenario_date < "2026-11-05":
            return False, "SCENARIO_DATE_NOT_REACHED", None
        if any(source_id not in states or states[source_id]["status"] != "applied"
               for source_id in ("NOV001", "NOV002", "NOV003")):
            return False, "SCENARIO_EVENTS_INCOMPLETE", None
        if not event or event["status"] != "not_applied":
            return False, "EVENT_STATE_UNAVAILABLE", None
        return True, None, None

    def apply_scenario_event(
        self, session_id: str, source_entry_id: str, idempotency_key: str,
        authorized_operation_id: str | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if source_entry_id not in SCENARIO_EVENT_GROUPS:
            return None, "SCENARIO_EVENT_NOT_FOUND"
        if authorized_operation_id and source_entry_id != "NOV002":
            return None, "RED_REPAYMENT_AUTH_REQUIRED"
        if source_entry_id in {"NOV002", "NOV003"} and not authorized_operation_id:
            return None, "RED_REPAYMENT_AUTH_REQUIRED"
        source_ids = SCENARIO_EVENT_GROUPS[source_entry_id]
        sources = self._scenario_event_rows()
        if any(source_id not in sources for source_id in source_ids):
            return None, "SCENARIO_EVENT_NOT_FOUND"
        with self._connection(session_id) as connection:
            if self.is_sqlite:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute(self._sql("SELECT id FROM sessions WHERE id = ? FOR UPDATE"), (session_id,))
            session = connection.execute(self._sql(
                "SELECT scenario_date FROM sessions WHERE id = ?"), (session_id,)).fetchone()
            if not session:
                return None, "SESSION_NOT_FOUND"
            security = connection.execute(self._sql(
                "SELECT locked_until FROM visitor_security WHERE session_id = ?"), (session_id,)).fetchone()
            if security and security["locked_until"] and self._datetime(security["locked_until"]) > self.clock():
                return None, "SESSION_LOCKED"
            if authorized_operation_id:
                authorization = connection.execute(self._sql(
                    "SELECT id, operation_type, recipient, purpose, amount_cents, verified, confirmed, requires_verification, "
                    "status, receipt, idempotency_key, task_id, step_id FROM operations WHERE session_id = ? AND id = ?"),
                    (session_id, authorized_operation_id),
                ).fetchone()
                if (not authorization or authorization["operation_type"] != "repayment"
                        or authorization["amount_cents"] != 284000
                        or not authorization["requires_verification"]
                        or not authorization["verified"] or not authorization["confirmed"]):
                    return None, "RED_REPAYMENT_AUTH_REQUIRED"
                if authorization["receipt"]:
                    receipt = authorization["receipt"] if isinstance(authorization["receipt"], dict) else json.loads(authorization["receipt"])
                    return receipt, None
                if authorization["status"] != "pending" or authorization["idempotency_key"] != idempotency_key:
                    return None, "RED_REPAYMENT_AUTH_REQUIRED"
                if authorization["task_id"]:
                    task = connection.execute(self._sql(
                        "SELECT status, step_state FROM task_chains WHERE session_id = ? AND id = ?"),
                        (session_id, authorization["task_id"]),
                    ).fetchone()
                    steps = (task["step_state"] if isinstance(task["step_state"], dict)
                             else json.loads(task["step_state"])) if task else {}
                    if (not task or task["status"] != "active"
                            or steps.get(authorization["step_id"], {}).get("status") != "awaiting_authorization"):
                        return None, "TASK_STEP_NOT_ACTIVE"
            scenario_date = session["scenario_date"]
            scenario_date = scenario_date.isoformat() if not isinstance(scenario_date, str) else scenario_date
            if scenario_date < sources[source_ids[0]]["posting_date"]:
                return None, "SCENARIO_DATE_NOT_REACHED"
            placeholders = ", ".join("?" for _ in source_ids)
            applications = connection.execute(self._sql(
                "SELECT source_entry_id, status, operation_id FROM scenario_event_applications "
                f"WHERE session_id = ? AND source_entry_id IN ({placeholders})"),
                (session_id, *source_ids),
            ).fetchall()
            states = {row["source_entry_id"]: row for row in applications}
            if len(states) != len(source_ids):
                return None, "SCENARIO_EVENT_STATE_UNAVAILABLE"
            if all(states[source_id]["status"] == "applied" for source_id in source_ids):
                operation = connection.execute(self._sql(
                    "SELECT receipt FROM operations WHERE session_id = ? AND id = ?"),
                    (session_id, states[source_ids[0]]["operation_id"]),
                ).fetchone()
                if not operation or not operation["receipt"]:
                    return None, "EVENT_RESULT_UNKNOWN"
                receipt = operation["receipt"] if isinstance(operation["receipt"], dict) else json.loads(operation["receipt"])
                return receipt, None
            if any(states[source_id]["status"] != "not_applied" for source_id in source_ids):
                return None, "SCENARIO_EVENT_STATE_INCONSISTENT"
            required = ("NOV001",) if source_entry_id == "NOV002" else ()
            if source_entry_id == "NOV005":
                required = ("NOV001", "NOV002", "NOV003", "NOV004")
            if required:
                required_marks = ", ".join("?" for _ in required)
                prior = connection.execute(self._sql(
                    "SELECT source_entry_id, status FROM scenario_event_applications WHERE session_id = ? "
                    f"AND source_entry_id IN ({required_marks})"),
                    (session_id, *required),
                ).fetchall()
                if len(prior) != len(required) or any(row["status"] != "applied" for row in prior):
                    return None, "SCENARIO_EVENTS_INCOMPLETE"
            if not authorized_operation_id:
                existing = connection.execute(self._sql(
                    "SELECT id, operation_type, receipt FROM operations WHERE session_id = ? AND idempotency_key = ?"),
                    (session_id, idempotency_key),
                ).fetchone()
                if existing:
                    if existing["operation_type"] == "scenario_event" and existing["receipt"]:
                        receipt = existing["receipt"] if isinstance(existing["receipt"], dict) else json.loads(existing["receipt"])
                        return receipt, None
                    return None, "IDEMPOTENCY_KEY_REUSED"
            operation_id = authorized_operation_id or str(uuid4())
            amount_cents = (self._cents(sources["NOV002"]["amount_cny"])
                            if source_entry_id in {"NOV002", "NOV003"}
                            else sum(self._cents(sources[source_id]["amount_cny"]) for source_id in source_ids))
            summary = {"source_entry_ids": list(source_ids), "scenario_date": scenario_date,
                       "amount_cents": amount_cents}
            summary_hash = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            event_type = "工资入账" if source_entry_id == "NOV001" else "信用卡还款" if source_entry_id == "NOV002" else "模拟报销入账"
            if not authorized_operation_id:
                connection.execute(self._sql(
                    "INSERT INTO operations (id, session_id, recipient, amount_cents, purpose, summary_hash, "
                    "verified, confirmed, requires_verification, status, operation_type, created_at, idempotency_key) "
                    "VALUES (?, ?, ?, ?, ?, ?, FALSE, TRUE, FALSE, 'pending', 'scenario_event', ?, ?)"),
                    (operation_id, session_id, "林晚", amount_cents, event_type, summary_hash,
                     self.clock().isoformat(), idempotency_key),
                )
            balances = {}
            for source_id in source_ids:
                source = sources[source_id]
                account_suffix = " FOR UPDATE" if not self.is_sqlite else ""
                account = connection.execute(self._sql(
                    "SELECT balance_cents FROM accounts WHERE session_id = ? AND name = ?" + account_suffix),
                    (session_id, source["account"]),
                ).fetchone()
                if not account:
                    return self._record_scenario_failure(connection, session_id, operation_id, event_type,
                                                         source_ids, "SOURCE_ACCOUNT_NOT_FOUND")
                amount = self._cents(source["amount_cny"])
                if source["account_type"] == "credit_card" and source["direction"] == "payment":
                    balance = account["balance_cents"] - amount
                elif source["direction"] == "credit":
                    balance = account["balance_cents"] + amount
                elif source["direction"] == "debit":
                    balance = account["balance_cents"] - amount
                else:
                    balance = -1
                expected_balance = self._optional_cents(
                    source["card_balance_after_cny"] if source["account_type"] == "credit_card"
                    else source["bank_balance_after_cny"]
                )
                if balance < 0 or balance != expected_balance:
                    return self._record_scenario_failure(connection, session_id, operation_id, event_type,
                                                         source_ids, "SOURCE_BALANCE_MISMATCH")
                balances[source["account"]] = balance
            transaction_ids = []
            for source_id in source_ids:
                source = sources[source_id]
                connection.execute(self._sql(
                    "UPDATE accounts SET balance_cents = ? WHERE session_id = ? AND name = ?"),
                    (balances[source["account"]], session_id, source["account"]),
                )
                next_order = connection.execute(self._sql(
                    "SELECT COALESCE(MAX(ledger_order), -1) + 1 AS next_order FROM transactions WHERE session_id = ?"),
                    (session_id,),
                ).fetchone()["next_order"]
                transaction_ids.append(self._insert_transaction(connection, session_id, source, next_order))
                connection.execute(self._sql(
                    "UPDATE scenario_event_applications SET status = 'applied', operation_id = ?, applied_at = ? "
                    "WHERE session_id = ? AND source_entry_id = ? AND status = 'not_applied'"),
                    (operation_id, self.clock().isoformat(), session_id, source_id),
                )
            receipt = {
                "operation_id": operation_id, "operation_type": event_type, "status": "succeeded",
                "source_entry_ids": list(source_ids), "transaction_ids": transaction_ids,
                "amount_cents": amount_cents, "balances_cents": balances,
                "scenario_date": scenario_date, "completed_at": self.clock().isoformat(), "demo": True,
            }
            if authorized_operation_id:
                receipt["summary"] = {
                    "recipient": authorization["recipient"], "amount_cents": authorization["amount_cents"],
                    "purpose": authorization["purpose"], "source_account": "借记卡6688",
                    "risk_level": "RED",
                }
            connection.execute(self._sql(
                "UPDATE operations SET receipt = ?, status = 'succeeded' WHERE session_id = ? AND id = ?"),
                (json.dumps(receipt, ensure_ascii=False), session_id, operation_id),
            )
            self._write_audit(connection, session_id, operation_id, "scenario_event_apply", "succeeded",
                              parameters={"source_entry_ids": list(source_ids), "scenario_date": scenario_date},
                              receipt=receipt)
        return receipt, None

    def _record_scenario_failure(
        self, connection: Any, session_id: str, operation_id: str, event_type: str,
        source_ids: tuple[str, ...], error_code: str,
    ) -> tuple[dict[str, Any], None]:
        status = "pending_verification" if error_code == "SOURCE_ACCOUNT_NOT_FOUND" else "failed"
        receipt = {
            "operation_id": operation_id, "operation_type": event_type, "status": status,
            "source_entry_ids": list(source_ids), "error_code": error_code,
            "message": "结果待核实，请先查询同一操作 ID。" if status == "pending_verification" else "模拟事件未应用，资金状态未改变。",
            "completed_at": self.clock().isoformat(), "demo": True,
        }
        connection.execute(self._sql(
            "UPDATE operations SET receipt = ?, status = ? WHERE session_id = ? AND id = ?"),
            (json.dumps(receipt, ensure_ascii=False), status, session_id, operation_id),
        )
        self._write_audit(connection, session_id, operation_id, "scenario_event_apply", status,
                          parameters={"source_entry_ids": list(source_ids)}, receipt=receipt,
                          error_code=error_code)
        return receipt, None

    def reset_generation(self, session_id: str) -> int:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT reset_generation FROM sessions WHERE id = ?"), (session_id,)).fetchone()
        return row["reset_generation"] if row else 0

    def _ledger_rows(self) -> list[dict[str, str]]:
        with self.ledger_path.open(encoding="utf-8-sig", newline="") as source:
            return list(csv.DictReader(source))

    @staticmethod
    def _cents(value: str) -> int:
        return int(Decimal(value or "0") * 100)

    @classmethod
    def _optional_cents(cls, value: str) -> int | None:
        return cls._cents(value) if value else None

    @staticmethod
    def _datetime(value: str | datetime) -> datetime:
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    def session_id_for_cookie(self, cookie: str) -> str | None:
        cookie_hash = hashlib.sha256(cookie.encode()).hexdigest()
        with self._connection() as connection:
            row = connection.execute(
                self._sql("SELECT id, expires_at FROM sessions WHERE cookie_hash = ?"), (cookie_hash,)
            ).fetchone()
        if not row:
            return None
        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at <= datetime.now(UTC):
            return None
        return row["id"]

    def accounts(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection(session_id) as connection:
            rows = connection.execute(
                self._sql("SELECT id, name, account_type, balance_cents FROM accounts WHERE session_id = ? ORDER BY name"),
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def account(self, session_id: str, account_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            row = connection.execute(
                self._sql("SELECT id, name, account_type, balance_cents FROM accounts WHERE session_id = ? AND id = ?"),
                (session_id, account_id),
            ).fetchone()
        return dict(row) if row else None

    def transaction(self, session_id: str, transaction_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            row = connection.execute(
                self._sql("SELECT id, source_entry_id, posting_date, posting_time, account, account_type, direction, amount_cents, description, prd_category, initial_category, allocation, payment_channel, bank_balance_after_cents, notes, entry_type, gross_amount_cents, include_in_monthly_spend, card_balance_after_cents FROM transactions WHERE session_id = ? AND id = ?"),
                (session_id, transaction_id),
            ).fetchone()
        return dict(row) if row else None

    def correct_team_event_category(self, session_id: str, operation_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            transaction = connection.execute(self._sql(
                "SELECT initial_category, amount_cents FROM transactions "
                "WHERE session_id = ? AND source_entry_id = 'TX002'"
            ), (session_id,)).fetchone()
            if not transaction or (transaction["initial_category"], transaction["amount_cents"]) != ("餐饮", 318000):
                return None
            connection.execute(self._sql(
                "INSERT INTO bill_category_corrections "
                "(session_id, source_entry_id, original_category, new_category, corrected_at) "
                "VALUES (?, 'TX002', '餐饮', '团建垫付（待报销）', ?) "
                "ON CONFLICT (session_id, source_entry_id) DO NOTHING"
            ), (session_id, self.clock().isoformat()))
            row = connection.execute(self._sql(
                "SELECT source_entry_id, original_category, new_category, corrected_at "
                "FROM bill_category_corrections WHERE session_id = ? AND source_entry_id = 'TX002'"
            ), (session_id,)).fetchone()
            result = dict(row) if row else None
            if result:
                self._write_audit(connection, session_id, operation_id, "bill_correction", "succeeded",
                                  parameters={"source_entry_id": "TX002", "new_category": result["new_category"]},
                                  confirmation={"confirmed": True}, receipt=result)
        return result

    def bill_summary(self, session_id: str, month: str) -> dict[str, Any]:
        with self._connection(session_id) as connection:
            totals = connection.execute(self._sql(
                "SELECT COUNT(CASE WHEN entry_type <> 'statement_snapshot' THEN 1 END) AS transaction_count, "
                "SUM(CASE WHEN include_in_monthly_spend THEN 1 ELSE 0 END) AS spend_transaction_count, "
                "SUM(CASE WHEN include_in_monthly_spend THEN amount_cents ELSE 0 END) AS monthly_spend_cents "
                "FROM transactions WHERE session_id = ? AND substr(posting_date, 1, 7) = ?"
            ), (session_id, month)).fetchone()
            categories = connection.execute(self._sql(
                "SELECT COALESCE(c.new_category, t.initial_category) AS category, SUM(t.amount_cents) AS amount_cents "
                "FROM transactions t LEFT JOIN bill_category_corrections c "
                "ON c.session_id = t.session_id AND c.source_entry_id = t.source_entry_id "
                "WHERE t.session_id = ? AND substr(t.posting_date, 1, 7) = ? "
                "AND t.include_in_monthly_spend = TRUE GROUP BY COALESCE(c.new_category, t.initial_category) "
                "ORDER BY category"
            ), (session_id, month)).fetchall()
            candidates = connection.execute(self._sql(
                "SELECT t.source_entry_id, t.posting_date, t.description, t.amount_cents, "
                "t.initial_category, COALESCE(c.new_category, t.initial_category) AS category, "
                "t.notes, c.original_category, c.new_category, c.corrected_at "
                "FROM transactions t LEFT JOIN bill_category_corrections c "
                "ON c.session_id = t.session_id AND c.source_entry_id = t.source_entry_id "
                "WHERE t.session_id = ? AND substr(t.posting_date, 1, 7) = ? "
                "AND t.include_in_monthly_spend = TRUE AND t.amount_cents > 100000 "
                "ORDER BY t.posting_date, t.ledger_order"
            ), (session_id, month)).fetchall()
        return {
            "month": month,
            "transaction_count": totals["transaction_count"],
            "spend_transaction_count": totals["spend_transaction_count"],
            "monthly_spend_cents": totals["monthly_spend_cents"] or 0,
            "categories": [dict(row) for row in categories],
            "anomalies": [{
                "label": "已识别房租" if row["initial_category"] == "房租" else "高金额待核对",
                "transaction": dict(row),
                "basis": ("冻结流水分类为房租；描述注明押一付三并覆盖 2026-10 至 2026-12。"
                          if row["initial_category"] == "房租" else "单笔金额超过 ¥1,000"),
                "uncertainty": ("依据冻结流水的类别和描述展示，不据此判断重复交易或欺诈；后续支付以之后账单为准。"
                                if row["initial_category"] == "房租" else
                                "仅按金额阈值提示关注；不确定其分类是否合适，也不代表欺诈或银行确认事实。"),
            } for row in candidates],
            "demo": True,
        }

    def subscription(self, session_id: str, subscription_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            row = connection.execute(
                self._sql("SELECT id, source_id, merchant, amount_cents, period, consecutive_charges, last_used_date, status FROM subscriptions WHERE session_id = ? AND id = ?"),
                (session_id, subscription_id),
            ).fetchone()
            cancellation = connection.execute(self._sql(
                "SELECT receipt FROM subscription_cancellations WHERE session_id = ? AND subscription_id = ?"),
                (session_id, subscription_id),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        if result["source_id"] != "SUB-CLOUD":
            result["consecutive_charges"] = None
            result["last_used_date"] = None
        if cancellation:
            value = cancellation["receipt"]
            result["cancellation_receipt"] = value if isinstance(value, dict) else json.loads(value)
        return result

    def subscriptions(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection(session_id) as connection:
            rows = connection.execute(self._sql(
                "SELECT id, source_id, merchant, amount_cents, period, consecutive_charges, last_used_date, status FROM subscriptions WHERE session_id = ? ORDER BY merchant"),
                (session_id,),
            ).fetchall()
        subscriptions = []
        for row in rows:
            item = dict(row)
            if item["source_id"] == "SUB-CLOUD":
                last_used = date.fromisoformat(str(item["last_used_date"]))
                item["days_since_last_use"] = (self.clock().date() - last_used).days
                item["assessment"] = "疑似" if item["amount_cents"] == 3000 and item["period"] == "monthly" and item["consecutive_charges"] == 7 and item["days_since_last_use"] >= 90 else "普通"
            else:
                item["consecutive_charges"] = None
                item["last_used_date"] = None
                item["days_since_last_use"] = None
                item["assessment"] = "普通"
            subscription = self.subscription(session_id, item["id"])
            if subscription and "cancellation_receipt" in subscription:
                item["cancellation_receipt"] = subscription["cancellation_receipt"]
            subscriptions.append(item)
        return subscriptions

    @staticmethod
    def _simulate_subscription_cancel(subscription_id: str) -> bool:
        return bool(subscription_id)

    def cancel_subscription(self, session_id: str, subscription_id: str, confirmed: bool,
                            *, allow_failed_reissue: bool = False) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            lock = " FOR UPDATE" if not self.is_sqlite else ""
            row = connection.execute(self._sql(
                "SELECT id, source_id, merchant, status FROM subscriptions WHERE session_id = ? AND id = ?" + lock),
                (session_id, subscription_id),
            ).fetchone()
            if not row or row["source_id"] != "SUB-CLOUD" or row["merchant"] != "云盘":
                return None
            operation_id = str(uuid4())
            existing = connection.execute(self._sql(
                "SELECT id, status, receipt FROM subscription_cancellations "
                "WHERE session_id = ? AND subscription_id = ? ORDER BY completed_at DESC LIMIT 1"),
                (session_id, subscription_id),
            ).fetchone()
            reissue = False
            if existing:
                value = existing["receipt"]
                receipt = value if isinstance(value, dict) else json.loads(value)
                if not (allow_failed_reissue and receipt.get("status") == "failed"):
                    operation_id = receipt["operation_id"]
                    self._write_audit(connection, session_id, operation_id, "subscription_cancel", "replayed",
                                      parameters={"subscription_id": subscription_id},
                                      confirmation={"confirmed": confirmed}, receipt=receipt)
                    return {"status": receipt["status"], "operation_id": operation_id, "receipt": receipt, "demo": True}
                self._write_audit(connection, session_id, receipt["operation_id"], "subscription_cancel",
                                  "reissue_authorized",
                                  parameters={"subscription_id": subscription_id,
                                              "new_operation_id": operation_id},
                                  confirmation={"confirmed": confirmed}, receipt=receipt)
                reissue = True
            if not confirmed:
                self._write_audit(connection, session_id, operation_id, "subscription_cancel", "confirmation_required",
                                  parameters={"subscription_id": subscription_id},
                                  confirmation={"confirmed": False}, error_code="CONFIRMATION_REQUIRED")
                return {"status": "confirmation_required", "operation_id": operation_id, "demo": True}
            completed_at = self.clock().isoformat()
            succeeded = self._simulate_subscription_cancel(subscription_id)
            receipt = {"operation_id": operation_id, "operation_type": "模拟订阅取消",
                       "subscription_id": subscription_id, "status": "succeeded" if succeeded else "failed",
                       "message": "模拟订阅已取消。" if succeeded else "模拟取消失败，请待核实。",
                       "completed_at": completed_at, "demo": True}
            if succeeded:
                connection.execute(self._sql(
                    "UPDATE subscriptions SET status = 'cancelled' WHERE session_id = ? AND id = ? AND status = 'active'"),
                    (session_id, subscription_id),
                )
            if reissue:
                connection.execute(self._sql(
                    "UPDATE subscription_cancellations SET id = ?, status = ?, completed_at = ?, receipt = ? "
                    "WHERE session_id = ? AND id = ?"),
                    (operation_id, receipt["status"], completed_at,
                     json.dumps(receipt, ensure_ascii=False), session_id, existing["id"]),
                )
            else:
                connection.execute(self._sql(
                    "INSERT INTO subscription_cancellations (id, session_id, subscription_id, status, completed_at, receipt) VALUES (?, ?, ?, ?, ?, ?)"),
                    (operation_id, session_id, subscription_id, receipt["status"], completed_at, json.dumps(receipt, ensure_ascii=False)),
                )
            self._write_audit(connection, session_id, operation_id, "subscription_cancel", receipt["status"],
                              parameters={"subscription_id": subscription_id},
                              confirmation={"confirmed": True}, receipt=receipt,
                              error_code=receipt.get("error_code"))
        return {"status": receipt["status"], "operation_id": operation_id, "receipt": receipt, "demo": True}

    def intent_context(self, session_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql("SELECT intent, slots FROM intent_contexts WHERE session_id = ?"), (session_id,)).fetchone()
        if not row:
            return None
        slots = row["slots"]
        return {"intent": row["intent"], "slots": slots if isinstance(slots, dict) else json.loads(slots)}

    def save_intent_context(self, session_id: str, intent: str, slots: dict[str, Any]) -> None:
        with self._connection(session_id) as connection:
            prior = connection.execute(self._sql(
                "SELECT intent, slots FROM intent_contexts WHERE session_id = ?"), (session_id,)
            ).fetchone()
            prior_slots = prior["slots"] if prior and isinstance(prior["slots"], dict) else (
                json.loads(prior["slots"]) if prior else {}
            )
            context_parameters_changed = (
                prior is not None and prior["intent"] == intent == "transfer"
                and any(prior_slots.get(key) != slots.get(key)
                        for key in ("recipient", "amount_cents", "purpose", "contact_id")
                        if key in prior_slots or key in slots)
            )
            slots_value = "?" if self.is_sqlite else "CAST(? AS jsonb)"
            statement = f"INSERT INTO intent_contexts (session_id, intent, slots) VALUES (?, ?, {slots_value}) ON CONFLICT (session_id) DO UPDATE SET intent = excluded.intent, slots = excluded.slots"
            connection.execute(self._sql(statement), (session_id, intent, json.dumps(slots, ensure_ascii=False)))
            if intent == "transfer" and ("recipient" in slots or "amount_cents" in slots):
                rows = connection.execute(self._sql("SELECT id, recipient, amount_cents, purpose FROM operations WHERE session_id = ? AND status = 'pending' AND receipt IS NULL"), (session_id,)).fetchall()
                for row in rows:
                    changed = (("recipient" in slots and row["recipient"] != slots["recipient"])
                               or ("amount_cents" in slots and row["amount_cents"] != slots["amount_cents"])
                               or ("purpose" in slots and row["purpose"] != slots["purpose"])
                               or context_parameters_changed)
                    if not changed:
                        continue
                    receipt = {"operation_id": row["id"], "operation_type": "即时转账", "status": "superseded", "message": "参数已变化，旧确认已失效。", "demo": True}
                    receipt_value = "?" if self.is_sqlite else "CAST(? AS jsonb)"
                    statement = f"UPDATE operations SET verified = FALSE, confirmed = FALSE, receipt = {receipt_value}, status = 'superseded' WHERE session_id = ? AND id = ?"
                    connection.execute(self._sql(statement),
                                       (json.dumps(receipt, ensure_ascii=False), session_id, row["id"]))
                    audit_slots = "?, ?" if self.is_sqlite else "CAST(? AS jsonb), CAST(? AS jsonb)"
                    connection.execute(self._sql(
                        "INSERT INTO operation_audit (operation_id, session_id, action, status, parameters, confirmation, receipt, occurred_at) "
                        f"VALUES (?, ?, 'intent_update', 'superseded', {audit_slots}, ?, ?)"),
                        (row["id"], session_id,
                         json.dumps({"recipient": row["recipient"], "amount_cents": row["amount_cents"]}, ensure_ascii=False),
                         json.dumps({"confirmed": False}, ensure_ascii=False),
                         json.dumps(receipt, ensure_ascii=False), self.clock().isoformat()))

    def create_transfer(self, session_id: str, summary: dict[str, Any], idempotency_key: str,
                        operation_type: str = "transfer", task_id: str | None = None,
                        step_id: str | None = None) -> tuple[str, bool, bool, dict[str, Any], str]:
        operation_id = str(uuid4())
        with self._connection(session_id) as connection:
            if not self.is_sqlite:
                connection.execute(self._sql("SELECT id FROM sessions WHERE id = ? FOR UPDATE"), (session_id,))
            security = connection.execute(self._sql("SELECT locked_until FROM visitor_security WHERE session_id = ?"), (session_id,)).fetchone()
            if security and security["locked_until"] and self._datetime(security["locked_until"]) > self.clock():
                return "", False, False, summary, ""
            if task_id:
                task = connection.execute(self._sql(
                    "SELECT status, step_state FROM task_chains WHERE session_id = ? AND id = ?"),
                    (session_id, task_id),
                ).fetchone()
                steps = (task["step_state"] if isinstance(task["step_state"], dict)
                         else json.loads(task["step_state"])) if task else {}
                if (not task or task["status"] != "active" or not step_id or step_id not in steps
                        or steps[step_id]["status"] not in {"ready", "awaiting_authorization"}):
                    return "", False, False, summary, ""
            existing = connection.execute(
                self._sql("SELECT id, summary_hash, requires_verification, operation_type, task_id, step_id FROM operations WHERE session_id = ? AND idempotency_key = ?"),
                (session_id, idempotency_key),
            ).fetchone()
            if existing:
                resolved_summary = {**summary, "risk_level": "RED" if existing["requires_verification"] else "YELLOW"}
                resolved_hash = hashlib.sha256(json.dumps(resolved_summary, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                if existing["summary_hash"] == resolved_hash and existing["operation_type"] == operation_type:
                    self._write_audit(connection, session_id, existing["id"], "transfer_prepare", "prepared", parameters=resolved_summary)
                same_parameters = (existing["summary_hash"] == resolved_hash and existing["operation_type"] == operation_type
                                   and existing["task_id"] == task_id and existing["step_id"] == step_id)
                return existing["id"], True, same_parameters, resolved_summary, resolved_hash
            if task_id and steps[step_id]["status"] != "ready":
                return "", False, False, summary, ""
            daily_total = connection.execute(
                self._sql("SELECT COALESCE(SUM(amount_cents), 0) AS total FROM operations WHERE session_id = ? AND date(created_at) = date(?) AND status = 'succeeded'"),
                (session_id, self.clock().isoformat()),
            ).fetchone()["total"]
            requires_verification = (operation_type == "repayment" or summary["amount_cents"] > 100000
                                     or daily_total + summary["amount_cents"] > 100000)
            summary = {**summary, "risk_level": "RED" if requires_verification else "YELLOW"}
            summary_hash = hashlib.sha256(json.dumps(summary, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            cursor = connection.execute(
                self._sql("INSERT INTO operations (id, session_id, recipient, amount_cents, purpose, summary_hash, created_at, idempotency_key, requires_verification, operation_type, task_id, step_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (session_id, idempotency_key) DO NOTHING"),
                (operation_id, session_id, summary["recipient"], summary["amount_cents"], summary["purpose"], summary_hash, self.clock().isoformat(), idempotency_key, requires_verification, operation_type, task_id, step_id),
            )
            existing = connection.execute(
                self._sql("SELECT id, summary_hash, requires_verification, operation_type, task_id, step_id FROM operations WHERE session_id = ? AND idempotency_key = ?"),
                (session_id, idempotency_key),
            ).fetchone()
            connection.execute(self._sql("INSERT OR IGNORE INTO visitor_security (session_id, failed_codes) VALUES (?, 0)"), (session_id,)) if self.is_sqlite else connection.execute(self._sql("INSERT INTO visitor_security (session_id, failed_codes) VALUES (?, 0) ON CONFLICT (session_id) DO NOTHING"), (session_id,))
            same_parameters = (existing["summary_hash"] == summary_hash and existing["operation_type"] == operation_type
                               and existing["task_id"] == task_id and existing["step_id"] == step_id)
            if same_parameters:
                self._write_audit(connection, session_id, existing["id"], "transfer_prepare", "prepared", parameters=summary)
        return existing["id"], cursor.rowcount == 0, same_parameters, summary, summary_hash

    def operation_type(self, session_id: str, operation_id: str) -> str | None:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT operation_type FROM operations WHERE session_id = ? AND id = ?"),
                (session_id, operation_id),
            ).fetchone()
        return row["operation_type"] if row else None

    def transfer_risk_features(self, session_id: str, recipient: str, amount_cents: int) -> dict[str, float]:
        with self._connection(session_id) as connection:
            account = connection.execute(self._sql(
                "SELECT balance_cents FROM accounts WHERE session_id = ? AND name = '借记卡6688'"),
                (session_id,),
            ).fetchone()
            transfers = connection.execute(self._sql(
                "SELECT recipient, created_at FROM operations WHERE session_id = ? "
                "AND operation_type = 'transfer' AND status = 'succeeded'"),
                (session_id,),
            ).fetchall()
            security = connection.execute(self._sql(
                "SELECT failed_codes FROM visitor_security WHERE session_id = ?"),
                (session_id,),
            ).fetchone()
        now = self.clock()
        recent = sum(0 <= (now - self._datetime(row["created_at"])).total_seconds() <= 600 for row in transfers)
        balance_cents = account["balance_cents"] if account else 0
        return {
            "new_recipient": float(not any(row["recipient"] == recipient for row in transfers)),
            "amount_ratio": min(amount_cents / max(balance_cents, 1), 2.0),
            "short_frequency": float(min(recent, 3)),
            "failed_codes": float(min(security["failed_codes"] if security else 0, 3)),
        }

    def execute_repayment(self, session_id: str, operation_id: str) -> tuple[dict[str, Any] | None, str | None]:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT idempotency_key FROM operations WHERE session_id = ? AND id = ? AND operation_type = 'repayment'"),
                (session_id, operation_id),
            ).fetchone()
        if not row:
            return None, "OPERATION_NOT_FOUND"
        return self.apply_scenario_event(session_id, "NOV002", row["idempotency_key"], operation_id)

    def verify_transfer(self, session_id: str, operation_id: str, code: str) -> str:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql("SELECT failed_codes, locked_until FROM visitor_security WHERE session_id = ?"), (session_id,)).fetchone()
            if row and row["locked_until"] and self._datetime(row["locked_until"]) > self.clock():
                self._write_audit(connection, session_id, operation_id, "transfer_verify", "rejected",
                                  error_code="SESSION_LOCKED")
                return "locked"
            operation = connection.execute(self._sql(
                "SELECT status, task_id, step_id FROM operations WHERE session_id = ? AND id = ?"),
                (session_id, operation_id),
            ).fetchone()
            if not operation:
                return "missing"
            if operation["status"] != "pending":
                return "finished"
            if operation["task_id"] and not self.task_operation_allowed(session_id, operation_id):
                return "task_inactive"
            failed_codes = row["failed_codes"] if row and not row["locked_until"] else 0
            if not hmac.compare_digest(code, "246810"):
                failed_codes += 1
                locked_until = self.clock() + timedelta(minutes=15) if failed_codes >= 3 else None
                connection.execute(
                    self._sql("INSERT INTO visitor_security (session_id, failed_codes, locked_until) VALUES (?, ?, ?) ON CONFLICT (session_id) DO UPDATE SET failed_codes = excluded.failed_codes, locked_until = excluded.locked_until"),
                    (session_id, failed_codes, locked_until.isoformat() if locked_until else None),
                )
                if locked_until:
                    connection.execute(self._sql(
                        "UPDATE operations SET verified = FALSE, confirmed = FALSE WHERE session_id = ? AND status = 'pending'"),
                        (session_id,),
                    )
                    connection.execute(self._sql("DELETE FROM security_rejections WHERE session_id = ?"), (session_id,))
                    self._write_audit(connection, session_id, operation_id, "circuit_breaker", "locked",
                                      parameters={"reason": "three_invalid_demo_codes", "locked_until": locked_until.isoformat()})
                self._write_audit(connection, session_id, operation_id, "transfer_verify", "rejected",
                                  confirmation={"verified": False}, error_code="INVALID_DEMO_CODE")
                return "locked" if locked_until else "invalid"
            connection.execute(self._sql("UPDATE operations SET verified = TRUE WHERE session_id = ? AND id = ?"), (session_id, operation_id))
            connection.execute(self._sql("INSERT INTO visitor_security (session_id, failed_codes) VALUES (?, 0) ON CONFLICT (session_id) DO UPDATE SET failed_codes = 0, locked_until = NULL"), (session_id,))
            self._write_audit(connection, session_id, operation_id, "transfer_verify", "succeeded",
                              confirmation={"verified": True})
        return "verified"

    def confirm_transfer(self, session_id: str, operation_id: str, summary_hash: str) -> str:
        with self._connection(session_id) as connection:
            row = connection.execute(
                self._sql("SELECT summary_hash, verified, confirmed, requires_verification, status, task_id FROM operations WHERE session_id = ? AND id = ?"),
                (session_id, operation_id),
            ).fetchone()
            if not row:
                return "missing"
            if row["status"] != "pending":
                return "finished"
            if row["task_id"] and not self.task_operation_allowed(session_id, operation_id):
                return "task_inactive"
            if row["summary_hash"] != summary_hash:
                return "changed"
            if row["requires_verification"] and not row["verified"]:
                return "unverified"
            if row["confirmed"]:
                return "confirmed"
            connection.execute(
                self._sql("UPDATE operations SET confirmed = TRUE WHERE session_id = ? AND id = ?"),
                (session_id, operation_id),
            )
            self._write_audit(connection, session_id, operation_id, "transfer_confirm", "succeeded",
                              confirmation={"summary_hash": summary_hash, "confirmed": True})
        return "confirmed"

    def execute_transfer(self, session_id: str, operation_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            if not self.is_sqlite:
                connection.execute(self._sql("SELECT id FROM sessions WHERE id = ? FOR UPDATE"), (session_id,))
            suffix = " FOR UPDATE" if not self.is_sqlite else ""
            operation = connection.execute(
                self._sql("SELECT * FROM operations WHERE session_id = ? AND id = ?" + suffix),
                (session_id, operation_id),
            ).fetchone()
            if (not operation or operation["operation_type"] != "transfer"
                    or (operation["requires_verification"] and not operation["verified"]) or not operation["confirmed"]):
                return None
            if operation["task_id"]:
                task = connection.execute(self._sql(
                    "SELECT status, step_state FROM task_chains WHERE session_id = ? AND id = ?"),
                    (session_id, operation["task_id"]),
                ).fetchone()
                steps = (task["step_state"] if isinstance(task["step_state"], dict)
                         else json.loads(task["step_state"])) if task else {}
                if (not task or task["status"] != "active"
                        or steps.get(operation["step_id"], {}).get("status") != "awaiting_authorization"):
                    return None
            if operation["receipt"]:
                return json.loads(operation["receipt"])
            summary = {"recipient": operation["recipient"], "amount_cents": operation["amount_cents"],
                       "purpose": operation["purpose"], "source_account": "借记卡6688",
                       "execution_timing": "立即",
                       "risk_level": "RED" if operation["requires_verification"] else "YELLOW"}
            completed_at = self.clock().isoformat()
            security = connection.execute(self._sql("SELECT locked_until FROM visitor_security WHERE session_id = ?"), (session_id,)).fetchone()
            if security and security["locked_until"] and self._datetime(security["locked_until"]) > self.clock():
                return None
            account = connection.execute(self._sql("SELECT balance_cents FROM accounts WHERE session_id = ? AND name = ?"), (session_id, "借记卡6688")).fetchone()
            if not account or operation["amount_cents"] > account["balance_cents"]:
                receipt = {"operation_id": operation_id, "operation_type": "即时转账", "status": "failed",
                           "error_code": "INSUFFICIENT_FUNDS", "message": "余额不足，请修改转账金额后重新发起。",
                           "amount_cents": operation["amount_cents"], "summary": summary,
                           "completed_at": completed_at, "demo": True}
                connection.execute(self._sql("UPDATE operations SET receipt = ?, status = 'failed' WHERE session_id = ? AND id = ?"),
                                   (json.dumps(receipt, ensure_ascii=False), session_id, operation_id))
                self._write_audit(connection, session_id, operation_id, "execute", "failed", receipt=receipt,
                                  error_code=receipt["error_code"])
                return receipt
            source = self._scenario_event_rows().get("NOV004")
            event_state = connection.execute(self._sql(
                "SELECT status, operation_id FROM scenario_event_applications "
                "WHERE session_id = ? AND source_entry_id = 'NOV004'" + suffix),
                (session_id,),
            ).fetchone()
            if not source or not event_state:
                receipt = {"operation_id": operation_id, "operation_type": "即时转账", "status": "pending_verification",
                           "error_code": "UNKNOWN_TOOL_RESULT", "message": "结果待核实，请先查询同一操作 ID。",
                           "summary": summary, "completed_at": completed_at,
                           "demo": True}
                connection.execute(self._sql("UPDATE operations SET receipt = ?, status = 'pending_verification' WHERE session_id = ? AND id = ?"),
                                   (json.dumps(receipt, ensure_ascii=False), session_id, operation_id))
                self._write_audit(connection, session_id, operation_id, "execute", "pending_verification",
                                  receipt=receipt, error_code=receipt["error_code"])
                return receipt
            if event_state["status"] == "applied":
                receipt = {"operation_id": operation_id, "operation_type": "即时转账", "status": "failed",
                           "error_code": "SIMULATED_TOOL_REJECTED", "message": "该冻结转账已由其他操作应用，当前操作未改变资金状态。",
                           "summary": summary, "completed_at": completed_at, "demo": True}
                connection.execute(self._sql("UPDATE operations SET receipt = ?, status = 'failed' WHERE session_id = ? AND id = ?"),
                                   (json.dumps(receipt, ensure_ascii=False), session_id, operation_id))
                self._write_audit(connection, session_id, operation_id, "execute", "failed",
                                  receipt=receipt, error_code=receipt["error_code"])
                return receipt
            session = connection.execute(self._sql(
                "SELECT scenario_date FROM sessions WHERE id = ?"), (session_id,)).fetchone()
            scenario_date = session["scenario_date"] if session else ""
            scenario_date = scenario_date.isoformat() if not isinstance(scenario_date, str) else scenario_date
            prerequisites = connection.execute(self._sql(
                "SELECT source_entry_id, status FROM scenario_event_applications WHERE session_id = ? "
                "AND source_entry_id IN ('NOV001', 'NOV002', 'NOV003')"), (session_id,)).fetchall()
            prerequisites_applied = len(prerequisites) == 3 and all(row["status"] == "applied" for row in prerequisites)
            frozen = {
                "amount_cents": self._cents(source["amount_cny"]),
                "direction": source["direction"], "allocation": source["allocation"],
                "account": source["account"], "account_type": source["account_type"],
                "entry_type": source["entry_type"], "posting_date": source["posting_date"],
                "posting_time": source["posting_time"], "payment_channel": source["payment_channel"],
            }
            if (event_state["status"] != "not_applied" or scenario_date < source["posting_date"]
                    or not prerequisites_applied or not _matches_frozen_transfer(
                        frozen, operation["amount_cents"], operation["recipient"], operation["purpose"]
                    )):
                receipt = {"operation_id": operation_id, "operation_type": "即时转账", "status": "failed",
                           "error_code": "SIMULATED_TOOL_REJECTED", "message": "模拟工具未确认该操作，资金状态未改变。",
                           "amount_cents": operation["amount_cents"], "summary": summary,
                           "completed_at": completed_at, "demo": True}
                connection.execute(self._sql("UPDATE operations SET receipt = ?, status = 'failed' WHERE session_id = ? AND id = ?"),
                                   (json.dumps(receipt, ensure_ascii=False), session_id, operation_id))
                self._write_audit(connection, session_id, operation_id, "execute", "failed", receipt=receipt,
                                  error_code=receipt["error_code"])
                return receipt
            expected_balance = self._optional_cents(source["bank_balance_after_cny"])
            resulting_balance = account["balance_cents"] - operation["amount_cents"]
            existing_transaction = connection.execute(self._sql(
                "SELECT id FROM transactions WHERE session_id = ? AND source_entry_id = 'NOV004'"),
                (session_id,),
            ).fetchone()
            if resulting_balance != expected_balance or existing_transaction:
                receipt = {"operation_id": operation_id, "operation_type": "即时转账", "status": "failed",
                           "error_code": "SIMULATED_TOOL_REJECTED", "message": "模拟工具未确认该操作，资金状态未改变。",
                           "amount_cents": operation["amount_cents"], "summary": summary,
                           "completed_at": completed_at, "demo": True}
                connection.execute(self._sql("UPDATE operations SET receipt = ?, status = 'failed' WHERE session_id = ? AND id = ?"),
                                   (json.dumps(receipt, ensure_ascii=False), session_id, operation_id))
                self._write_audit(connection, session_id, operation_id, "execute", "failed", receipt=receipt,
                                  error_code=receipt["error_code"])
                return receipt
            ledger_order = connection.execute(self._sql(
                "SELECT COALESCE(MAX(ledger_order), -1) + 1 AS next_order FROM transactions WHERE session_id = ?"),
                (session_id,),
            ).fetchone()["next_order"]
            transaction_id = self._insert_transaction(connection, session_id, source, ledger_order)
            connection.execute(self._sql(
                "UPDATE accounts SET balance_cents = ? WHERE session_id = ? AND name = ?"),
                (resulting_balance, session_id, "借记卡6688"),
            )
            connection.execute(self._sql(
                "UPDATE scenario_event_applications SET status = 'applied', operation_id = ?, applied_at = ? "
                "WHERE session_id = ? AND source_entry_id = 'NOV004' AND status = 'not_applied'"),
                (operation_id, completed_at, session_id),
            )
            receipt = {
                "operation_id": operation_id, "operation_type": "即时转账", "status": "succeeded",
                "transaction_id": transaction_id, "source_entry_ids": ["NOV004"],
                "recipient": operation["recipient"], "amount_cents": operation["amount_cents"],
                "purpose": operation["purpose"], "source_account": "借记卡6688",
                "summary": summary,
                "completed_at": f"{source['posting_date']}T{source['posting_time']}", "demo": True,
            }
            connection.execute(
                self._sql("UPDATE operations SET receipt = ?, status = 'succeeded' WHERE session_id = ? AND id = ?"),
                (json.dumps(receipt, ensure_ascii=False), session_id, operation_id),
            )
            self._write_audit(connection, session_id, operation_id, "execute", "succeeded", receipt=receipt)
        return receipt

    def transfer_receipt(self, session_id: str, operation_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            row = connection.execute(
                self._sql("SELECT receipt FROM operations WHERE session_id = ? AND id = ?"),
                (session_id, operation_id),
            ).fetchone()
            if not row:
                row = connection.execute(self._sql(
                    "SELECT receipt FROM subscription_cancellations WHERE session_id = ? AND id = ?"),
                    (session_id, operation_id),
                ).fetchone()
        if not row or not row["receipt"]:
            return None
        return row["receipt"] if isinstance(row["receipt"], dict) else json.loads(row["receipt"])

    def latest_receipt(self, session_id: str) -> dict[str, Any] | None:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql(
                "SELECT receipt, created_at AS occurred_at FROM operations "
                "WHERE session_id = ? AND receipt IS NOT NULL UNION ALL "
                "SELECT receipt, completed_at AS occurred_at FROM subscription_cancellations "
                "WHERE session_id = ? ORDER BY occurred_at DESC LIMIT 1"),
                (session_id, session_id),
            ).fetchone()
        if not row:
            return None
        return row["receipt"] if isinstance(row["receipt"], dict) else json.loads(row["receipt"])

    def cancel_transfer(self, session_id: str, operation_id: str) -> str:
        with self._connection(session_id) as connection:
            row = connection.execute(self._sql("SELECT * FROM operations WHERE session_id = ? AND id = ?"), (session_id, operation_id)).fetchone()
            if not row:
                return "missing"
            if row["receipt"]:
                return "finished"
            receipt = {"operation_id": operation_id,
                       "operation_type": "信用卡还款" if row["operation_type"] == "repayment" else "即时转账",
                       "status": "cancelled",
                       "summary": {"recipient": row["recipient"], "amount_cents": row["amount_cents"],
                                   "purpose": row["purpose"], "source_account": "借记卡6688",
                                   "execution_timing": "立即",
                                   "risk_level": "RED" if row["requires_verification"] else "YELLOW"},
                       "completed_at": self.clock().isoformat(), "demo": True}
            connection.execute(self._sql("UPDATE operations SET receipt = ?, status = 'cancelled' WHERE session_id = ? AND id = ?"),
                               (json.dumps(receipt, ensure_ascii=False), session_id, operation_id))
            self._write_audit(connection, session_id, operation_id, "transfer_cancel", "cancelled", receipt=receipt)
        return "cancelled"
