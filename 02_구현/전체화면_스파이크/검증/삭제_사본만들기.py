"""실제 연구 자료를 읽기 전용 온라인 백업으로 복사하고 무결성을 검증합니다."""

import argparse
import json
import shutil
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIVE_DATA = ROOT / "03_실행데이터/실제연구"
COPIES = ROOT / ".cache/삭제검증_사본"


def readonly(path):
    """원본을 생성하거나 수정하지 않는 URI 연결을 엽니다."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def counts(db):
    """사용자 테이블별 행 수를 읽습니다."""
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {
        n: db.execute('SELECT count(*) FROM "' + n.replace('"', '""') + '"').fetchone()[0]
        for n in names
    }


def make_copy(dest):
    """허용된 새 폴더에 DB 스냅샷과 일반 파일을 복사하고 원본 행 수와 대조합니다."""
    dest = Path(dest).resolve()
    if not dest.is_relative_to(COPIES.resolve()) or dest == COPIES.resolve() or dest.exists():
        raise ValueError(f"허용되지 않은 사본 경로: {dest}")
    files = sorted(p for p in LIVE_DATA.rglob("*") if p.is_file())
    summary = {"destination": str(dest), "databases": {}, "files": []}
    with ExitStack() as stack:
        sources = {}
        for p in files:
            if p.suffix == ".sqlite3":
                db = readonly(p)
                stack.callback(db.close)
                stack.enter_context(db)
                db.execute("BEGIN")
                db.execute("SELECT count(*) FROM sqlite_master").fetchone()
                sources[p] = db
        dest.mkdir(parents=True)
        for p in files:
            rel = p.relative_to(LIVE_DATA)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if p in sources:
                with closing(sqlite3.connect(target)) as backup:
                    sources[p].backup(backup)
                with closing(readonly(target)) as copy:
                    integrity = copy.execute("PRAGMA integrity_check").fetchall()
                    actual, expected = counts(copy), counts(sources[p])
                if integrity != [("ok",)] or actual != expected:
                    raise RuntimeError(f"사본 무결성 실패: {rel}")
                summary["databases"][str(rel)] = {
                    "integrity_check": integrity[0][0],
                    "source": expected,
                    "copy": actual,
                }
            elif not any(str(p).endswith(x) for x in ("-wal", "-shm", "-journal")):
                shutil.copy2(p, target)
                summary["files"].append(str(rel))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", type=Path)
    print(json.dumps(make_copy(parser.parse_args().dest), ensure_ascii=False, indent=2))
