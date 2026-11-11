"""최초의 빈 호출 장부만 준비한다. 기존 정책/이력/미정산 예약은 변경하지 않는다."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from 교안자료_준비 import PreparationError, prepare_materials


def _read_budget(path):
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            maximum, money, baseline = db.execute(
                "SELECT max_attempts,max_nano_usd,reset_after_rowid FROM budget_policy WHERE id=1"
            ).fetchone()
            count, unknown = db.execute(
                "SELECT COUNT(CASE WHEN rowid>? THEN 1 END),"
                "COUNT(CASE WHEN spent IS NULL THEN 1 END) FROM attempts",
                (baseline,),
            ).fetchone()
    except (sqlite3.Error, TypeError):
        raise PreparationError("existing_ledger_invalid: original preserved") from None
    return {
        "max_attempts": maximum,
        "max_usd": money / 1_000_000_000,
        "attempt_count": count,
        "unknown_count": unknown,
    }


def prepare_runtime(root, *, initialize=False, max_attempts=10000, max_usd=20):
    root = Path(root).resolve()
    materials = prepare_materials(root)
    folder = root / "03_실행데이터/실제연구"
    path = folder / "호출예산.sqlite3"
    created = False
    if not path.exists():
        if folder.exists() and any(folder.iterdir()):
            raise PreparationError("missing_existing_ledger: restore the original ledger first")
        if not initialize:
            raise PreparationError("not_initialized: run 실행준비.py --init on a fresh project")
        if any(type(value) is not int or value <= 0 for value in (max_attempts, max_usd)):
            raise PreparationError("invalid_limits")
        implementation = Path(__file__).resolve().parent / "02_구현/전체화면_스파이크"
        sys.path.insert(0, str(implementation))
        from 서버.호출예산 import CallBudget

        folder.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise PreparationError("destination_changed: inspect existing ledger") from None
        os.close(descriptor)
        budget = CallBudget(path)
        budget.reset_attempt_counter(max_attempts=max_attempts, max_usd=max_usd)
        created = True
    return {
        "materials": materials,
        "ledger_created": created,
        "budget": _read_budget(path),
        "environment_file_exists": (root / ".env").is_file(),
        "credentials_read_or_validated": False,
        "frontend_built": (root / "02_구현/전체화면_스파이크/화면/dist/index.html").is_file(),
        "actual_model_requests": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--init", action="store_true", help="새 환경에서만 최초 빈 장부 생성")
    mode.add_argument("--check", action="store_true", help="읽기 전용 검사 (기본)")
    parser.add_argument("--max-attempts", type=int, default=10000)
    parser.add_argument("--max-usd", type=int, default=20)
    args = parser.parse_args()
    try:
        result = prepare_runtime(
            Path(__file__).resolve().parent,
            initialize=args.init,
            max_attempts=args.max_attempts,
            max_usd=args.max_usd,
        )
    except PreparationError as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
