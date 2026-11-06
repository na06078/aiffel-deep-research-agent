"""SQLite 원자 예약. 금액은 정수 nano-USD이며 실제 청구가 아닌 앱 추정."""

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path


class BudgetError(Exception):
    """안전한 예산 거절."""


class CallBudget:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, reserved INTEGER NOT NULL, spent INTEGER, input_tokens INTEGER, output_tokens INTEGER)"
            )
            db.commit()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def reserve(self, input_bound, output_bound, context):
        if (
            type(input_bound) is not int
            or type(output_bound) is not int
            or not 0 <= input_bound <= 272000
            or output_bound <= 0
        ):
            raise BudgetError("invalid_bounds")
        amount = input_bound * 250 + output_bound * 1200
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            count, total = db.execute(
                "SELECT count(*), coalesce(sum(coalesce(spent,reserved)),0) FROM attempts"
            ).fetchone()
            if count >= 40 or total + amount > 1_000_000_000:
                raise BudgetError("budget_exceeded")
            attempt_id = uuid.uuid4().hex
            db.execute("INSERT INTO attempts(id,reserved) VALUES (?,?)", (attempt_id, amount))
            db.commit()
        return attempt_id

    def settle(self, attempt_id, input_tokens, output_tokens):
        if any(type(n) is not int or n < 0 for n in (input_tokens, output_tokens)):
            raise BudgetError("invalid_usage")
        # 입력 캐시 쓰기 1.25배까지 가정한 $0.25/M, 출력 $1.2/M.
        amount = input_tokens * 250 + output_tokens * 1200
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT reserved,spent FROM attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if row is None or row[1] is not None or amount > row[0]:
                raise BudgetError("invalid_settlement")
            db.execute(
                "UPDATE attempts SET spent=?,input_tokens=?,output_tokens=? WHERE id=?",
                (amount, input_tokens, output_tokens, attempt_id),
            )
            db.commit()
        return amount / 1_000_000_000

    def usage(self):
        with closing(self._connect()) as db:
            count, spent, reserved, inputs, outputs, unknown = db.execute(
                "SELECT count(*),coalesce(sum(spent),0),coalesce(sum(CASE WHEN spent IS NULL THEN reserved ELSE 0 END),0),coalesce(sum(input_tokens),0),coalesce(sum(output_tokens),0),coalesce(sum(CASE WHEN spent IS NULL THEN 1 ELSE 0 END),0) FROM attempts"
            ).fetchone()
        return {
            "attempt_count": count,
            "max_attempts": 40,
            "remaining_attempts": 40 - count,
            "spent_usd": spent / 1_000_000_000,
            "reserved_usd": reserved / 1_000_000_000,
            "max_usd": 1.0,
            "input_tokens": inputs,
            "output_tokens": outputs,
            "unknown_count": unknown,
        }
