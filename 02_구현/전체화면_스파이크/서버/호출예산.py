"""SQLite 원자 예약. 금액은 정수 nano-USD이며 실제 청구가 아닌 앱 추정."""

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

NANO_USD = 1_000_000_000
DEFAULT_MAX_ATTEMPTS = 40
DEFAULT_MAX_USD = 1


class BudgetError(Exception):
    """안전한 예산 거절."""


class CallBudget:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, reserved INTEGER NOT NULL, spent INTEGER, input_tokens INTEGER, output_tokens INTEGER)"
            )
            policy_exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='budget_policy'"
            ).fetchone()
            if policy_exists is None:
                # 기존 장부는 같은 40회/$1 기준으로 이관하고, 운영 변경은 명시적으로만 한다.
                db.execute(
                    "CREATE TABLE budget_policy (id INTEGER PRIMARY KEY CHECK(id=1), "
                    "max_attempts INTEGER NOT NULL CHECK(max_attempts>0), "
                    "max_nano_usd INTEGER NOT NULL CHECK(max_nano_usd>0), "
                    "reset_after_rowid INTEGER NOT NULL CHECK(reset_after_rowid>=0))"
                )
                db.execute(
                    "INSERT INTO budget_policy VALUES (1,?,?,0)",
                    (DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_USD * NANO_USD),
                )
            elif db.execute("SELECT count(*) FROM budget_policy WHERE id=1").fetchone()[0] != 1:
                raise BudgetError("invalid_policy")
            db.commit()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def reset_attempt_counter(self, *, max_attempts, max_usd):
        """호출 이력과 비용은 보존하며 새 호출 집계 기준을 설정한다."""
        if any(type(n) is not int or n <= 0 for n in (max_attempts, max_usd)):
            raise BudgetError("invalid_limits")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM attempts WHERE spent IS NULL LIMIT 1").fetchone():
                raise BudgetError("unsettled_attempts")
            baseline = db.execute("SELECT coalesce(max(rowid),0) FROM attempts").fetchone()[0]
            db.execute(
                "UPDATE budget_policy SET max_attempts=?,max_nano_usd=?,reset_after_rowid=? "
                "WHERE id=1",
                (max_attempts, max_usd * NANO_USD, baseline),
            )
            db.commit()

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
            limit, money, baseline = db.execute(
                "SELECT max_attempts,max_nano_usd,reset_after_rowid FROM budget_policy WHERE id=1"
            ).fetchone()
            count, total = db.execute(
                "SELECT count(CASE WHEN rowid>? THEN 1 END), "
                "coalesce(sum(coalesce(spent,reserved)),0) FROM attempts",
                (baseline,),
            ).fetchone()
            if count >= limit or total + amount > money:
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
        return amount / NANO_USD

    def usage(self):
        with closing(self._connect()) as db:
            db.execute("BEGIN")
            limit, money, baseline = db.execute(
                "SELECT max_attempts,max_nano_usd,reset_after_rowid FROM budget_policy WHERE id=1"
            ).fetchone()
            count, spent, reserved, inputs, outputs, unknown = db.execute(
                "SELECT count(CASE WHEN rowid>? THEN 1 END),coalesce(sum(spent),0),"
                "coalesce(sum(CASE WHEN spent IS NULL THEN reserved ELSE 0 END),0),"
                "coalesce(sum(input_tokens),0),coalesce(sum(output_tokens),0),"
                "coalesce(sum(CASE WHEN spent IS NULL THEN 1 ELSE 0 END),0) FROM attempts",
                (baseline,),
            ).fetchone()
        return {
            "attempt_count": count,
            "max_attempts": limit,
            "remaining_attempts": limit - count,
            "spent_usd": spent / NANO_USD,
            "reserved_usd": reserved / NANO_USD,
            "max_usd": money / NANO_USD,
            "input_tokens": inputs,
            "output_tokens": outputs,
            "unknown_count": unknown,
        }
