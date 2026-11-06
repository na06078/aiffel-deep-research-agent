import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from 서버.호출예산 import BudgetError, CallBudget


def rows(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT rowid,* FROM attempts ORDER BY rowid").fetchall()


def test_27회이력을남기고_0회100한도2달러로_초기화와재시작(tmp_path):
    path = tmp_path / "예산.sqlite3"
    budget = CallBudget(path)
    for _ in range(27):
        attempt = budget.reserve(10000, 4096, {})
        budget.settle(attempt, 100, 30)
    historical_rows = rows(path)
    previous = budget.usage()

    budget.reset_attempt_counter(max_attempts=100, max_usd=2)

    current = CallBudget(path).usage()
    assert current["attempt_count"] == 0
    assert current["max_attempts"] == current["remaining_attempts"] == 100
    assert current["max_usd"] == 2.0
    assert current["spent_usd"] == previous["spent_usd"] == pytest.approx(0.001647)
    assert current["input_tokens"] == previous["input_tokens"] == 2700
    assert current["output_tokens"] == previous["output_tokens"] == 810
    assert current["unknown_count"] == current["reserved_usd"] == 0
    assert rows(path) == historical_rows

    restarted = CallBudget(path)
    restarted.reserve(10000, 4096, {})
    assert restarted.usage()["attempt_count"] == 1
    assert restarted.usage()["remaining_attempts"] == 99
    assert rows(path)[:27] == historical_rows


def test_미정산호출이있으면_초기화거절과_정책이력보존(tmp_path):
    path = tmp_path / "불명.sqlite3"
    budget = CallBudget(path)
    budget.reserve(10000, 4096, {})
    before = budget.usage()
    historical_rows = rows(path)

    with pytest.raises(BudgetError, match="unsettled_attempts"):
        budget.reset_attempt_counter(max_attempts=100, max_usd=2)

    assert CallBudget(path).usage() == before
    assert rows(path) == historical_rows


def test_동시예약은_초기화후100회에서막고_재시작에도유지(tmp_path):
    path = tmp_path / "백회.sqlite3"
    budget = CallBudget(path)
    budget.reset_attempt_counter(max_attempts=100, max_usd=2)

    def reserve(_):
        try:
            return CallBudget(path).reserve(0, 1, {})
        except BudgetError as error:
            assert str(error) == "budget_exceeded"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(120)))
    assert len([result for result in results if result]) == 100
    current = CallBudget(path).usage()
    assert current["attempt_count"] == current["unknown_count"] == 100
    assert current["max_attempts"] == 100
    assert current["remaining_attempts"] == 0
    assert current["max_usd"] == 2.0
    assert len(rows(path)) == 100


def test_2달러한도도_불명비용을포함하고_횟수보다먼저막음(tmp_path):
    path = tmp_path / "이달러.sqlite3"
    budget = CallBudget(path)
    budget.reset_attempt_counter(max_attempts=100, max_usd=2)
    # 기존 계약의 1회 최악 추정 $0.0729152: $2 안에서는 27회, 28회는 초과.
    for _ in range(27):
        budget.reserve(272000, 4096, {})
    before = rows(path)
    with pytest.raises(BudgetError, match="budget_exceeded"):
        budget.reserve(272000, 4096, {})
    assert len(rows(path)) == 27
    assert rows(path) == before
    assert budget.usage()["remaining_attempts"] == 73


@pytest.mark.parametrize("attempts,money", [(0, 2), (100, 0), (True, 2), (100, 2.5)])
def test_잘못된한도는_정책과이력변경없이거절(tmp_path, attempts, money):
    path = tmp_path / "거절.sqlite3"
    budget = CallBudget(path)
    before = budget.usage()
    with pytest.raises(BudgetError, match="invalid_limits"):
        budget.reset_attempt_counter(max_attempts=attempts, max_usd=money)
    assert CallBudget(path).usage() == before
    assert rows(path) == []


def test_기존장부는_행그대로이관하고_정책행손실은복구로위장하지않음(tmp_path):
    path = tmp_path / "옛장부.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE attempts (id TEXT PRIMARY KEY, reserved INTEGER NOT NULL, "
            "spent INTEGER,input_tokens INTEGER,output_tokens INTEGER)"
        )
        db.execute("INSERT INTO attempts VALUES ('historical',7415200,61000,100,30)")
        db.commit()
    before = rows(path)
    current = CallBudget(path).usage()
    assert current["attempt_count"] == 1
    assert current["max_attempts"] == 40
    assert current["max_usd"] == 1.0
    assert rows(path) == before
    with closing(sqlite3.connect(path)) as db:
        db.execute("DELETE FROM budget_policy")
        db.commit()
    with pytest.raises(BudgetError, match="invalid_policy"):
        CallBudget(path)
    assert rows(path) == before
