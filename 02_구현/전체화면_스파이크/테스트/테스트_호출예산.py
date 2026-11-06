from concurrent.futures import ThreadPoolExecutor

import pytest

from 서버.호출예산 import BudgetError, CallBudget


def test_동시40회와_재시작_불명예약(tmp_path):
    path = tmp_path / "예산.sqlite3"
    CallBudget(path)

    def reserve(_):
        try:
            return CallBudget(path).reserve(8192, 4096, {})
        except BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(48)))
    assert len([r for r in results if r]) == 40
    state = CallBudget(path).usage()
    assert state["attempt_count"] == 40
    assert state["remaining_attempts"] == 0
    assert state["unknown_count"] == 40
    assert state["reserved_usd"] > 0


def test_정산은_잔여예약만풀고_시도는보존(tmp_path):
    path = tmp_path / "정산.sqlite3"
    budget = CallBudget(path)
    attempt = budget.reserve(10000, 4096, {})
    cost = budget.settle(attempt, 100, 30)
    assert cost == pytest.approx(0.000061)
    restarted = CallBudget(path).usage()
    assert restarted["attempt_count"] == 1
    assert restarted["input_tokens"] == 100
    assert restarted["output_tokens"] == 30
    assert restarted["reserved_usd"] == 0
    assert restarted["unknown_count"] == 0
    with pytest.raises(BudgetError, match="invalid_settlement"):
        budget.settle(attempt, 100, 30)


def test_불명예약도_동시달러상한에포함(tmp_path):
    path = tmp_path / "달러.sqlite3"
    CallBudget(path)

    def reserve(_):
        try:
            return CallBudget(path).reserve(272000, 4096, {})
        except BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(20)))
    # 1회 .0729152, 13회 .9478976, 14회 1.0208128 → 13회만 허용.
    assert len([r for r in results if r]) == 13
    state = CallBudget(path).usage()
    assert state["reserved_usd"] == pytest.approx(0.9478976)
    assert state["spent_usd"] == 0
    assert state["unknown_count"] == 13
    assert state["attempt_count"] == 13
