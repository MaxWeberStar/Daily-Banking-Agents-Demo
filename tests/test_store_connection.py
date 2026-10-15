from __future__ import annotations

import sys
from datetime import date, time
from types import ModuleType
from typing import Any

from app.store import Store, _matches_frozen_transfer


def test_postgres_connections_disable_prepared_statements_for_pooler(monkeypatch: Any) -> None:
    connect_args: dict[str, Any] = {}

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    psycopg = ModuleType("psycopg")
    psycopg.connect = lambda *args, **kwargs: connect_args.update(kwargs) or Connection()
    rows = ModuleType("psycopg.rows")
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows)

    store = Store.__new__(Store)
    store.database_url = "postgresql://pooler.example/test"
    store.is_sqlite = False
    with store._connection():
        pass

    assert connect_args["prepare_threshold"] is None


def test_frozen_transfer_matches_postgres_date_and_time_values() -> None:
    transaction = {
        "amount_cents": 200000,
        "direction": "debit",
        "allocation": "母亲",
        "account": "借记卡6688",
        "account_type": "deposit",
        "entry_type": "transfer",
        "posting_date": date(2026, 11, 5),
        "posting_time": time(9, 10),
        "payment_channel": "模拟转账",
    }

    assert _matches_frozen_transfer(transaction, 200000, "妈妈", "生活费")
