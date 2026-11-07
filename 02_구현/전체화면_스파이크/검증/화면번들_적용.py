"""검증된 후보 화면 번들을 사용자 실행본의 정적 폴더(화면/dist)로 교체한다.

서버를 재시작하지 않는다. 서버는 요청이 올 때마다 디스크의 정적 파일을 읽으므로
파일만 바꾸고 브라우저를 새로고침하면 새 화면이 열린다(기술 명세 26.7과 같은 절차).

교체 순서: ① 새 asset 복사 → ② index.html 원자적 교체 → ③ 옛 asset 삭제.
시작 전에 후보가 검증 때와 같은 바이트인지, dist가 착수 기준(또는 이미 후보와 같은 상태)인지
확인하고 하나라도 어긋나면 아무것도 바꾸지 않는다.
--apply 없이 실행하면 계획만 출력하는 드라이런이다.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
DEFAULT_OUT = SPIKE / "검증결과" / "화면개편" / "적용_번들확인.json"
RETRIES = 6  # Windows에서 서버가 파일을 읽는 순간의 일시적 공유 위반을 넘기기 위한 재시도 횟수


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(directory: Path) -> dict[str, str]:
    """폴더 안 모든 파일의 {상대 경로: SHA-256}."""
    return {
        p.relative_to(directory).as_posix(): sha256_of(p)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def make_plan(candidate: dict[str, str], current: dict[str, str]) -> dict[str, list[str]]:
    return {
        "new": sorted(set(candidate) - set(current)),
        "replaced": sorted(k for k in set(candidate) & set(current) if candidate[k] != current[k]),
        "removed": sorted(set(current) - set(candidate)),
    }


def plan_problems(plan: dict[str, list[str]]) -> list[str]:
    """정적 번들 교체로 볼 수 없는 변경(공용 파일 변경, assets 밖 삭제 등)이면 사유를 돌려준다."""
    problems = [f"새 파일이 assets 밖: {p}" for p in plan["new"] if not p.startswith("assets/")]
    problems += [f"삭제 대상이 assets 밖: {p}" for p in plan["removed"] if not p.startswith("assets/")]
    if plan["replaced"] != ["index.html"]:
        problems.append(f"교체 대상이 index.html 하나가 아님: {plan['replaced']}")
    return problems


def with_retries(action, description: str):
    """서버가 같은 파일을 읽는 순간의 PermissionError만 짧게 재시도한다."""
    for attempt in range(1, RETRIES + 1):
        try:
            return action()
        except PermissionError:
            if attempt == RETRIES:
                raise
            time.sleep(0.3)
    raise RuntimeError(description)  # 도달하지 않는다


def copy_atomically(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".적용중")
    shutil.copyfile(source, temporary)
    with_retries(lambda: os.replace(temporary, destination), f"교체 {destination.name}")


def http_get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def api_summary(base: str) -> dict:
    """적용 전후로 실행 기록이 그대로인지 보기 위한 읽기 전용 요약(GET만 사용)."""
    status, body = http_get(base + "/api/spike/snapshot")
    if status != 200:
        return {"error": f"HTTP {status}"}
    snapshot = json.loads(body)
    return {
        "runs": [[r["run_id"], r["status"], r["updated_at"]] for r in snapshot["runs"]],
        "current_run_id": snapshot["current_run_id"],
        "high_watermark_seq": snapshot["high_watermark_seq"],
        "attempt_count": (snapshot.get("budget") or {}).get("attempt_count"),
    }


def verify_served_bytes(base: str, candidate_dir: Path, candidate: dict[str, str]) -> dict:
    """서버가 후보와 같은 바이트를 내려주는지 모든 파일에서 대조한다."""
    mismatches = []
    for relative in candidate:
        # 에셋/ 같은 한글 경로는 퍼센트 인코딩해야 HTTP 요청줄(ASCII)로 보낼 수 있다.
        status, body = http_get(f"{base}/{quote(relative)}")
        if status != 200 or body != (candidate_dir / relative).read_bytes():
            mismatches.append({"path": relative, "status": status})
    status, body = http_get(base + "/")
    root_ok = status == 200 and body == (candidate_dir / "index.html").read_bytes()
    return {"files_checked": len(candidate), "mismatches": mismatches, "root_matches_index": root_ok}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="실제로 교체한다(없으면 드라이런)")
    parser.add_argument("--candidate", default=str(ROOT / ".cache/화면개편_후보"))
    parser.add_argument("--manifest", default=str(ROOT / ".cache/화면개편_후보_매니페스트.json"))
    parser.add_argument("--baseline", default=str(ROOT / ".cache/화면개편_dist기준.json"))
    parser.add_argument("--dist", default=str(SPIKE / "화면/dist"))
    parser.add_argument("--base", default="http://127.0.0.1:8768", help="서버 주소. 빈 문자열이면 HTTP 검증을 건너뛴다(리허설용)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    candidate_dir, dist_dir = Path(args.candidate), Path(args.dist)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    evidence: dict = {"started_at": datetime.now(UTC).isoformat(), "mode": "apply" if args.apply else "dry-run",
                      "dist": dist_dir.as_posix(), "server": args.base or None}

    def finish(status: str, code: int, **extra) -> int:
        evidence.update(extra, status=status, finished_at=datetime.now(UTC).isoformat())
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{status}  (증거: {out.as_posix()})")
        return code

    candidate = tree_hashes(candidate_dir)
    if candidate != manifest["files"]:
        return finish("중단: 후보 번들이 검증 때의 매니페스트와 다름", 1)
    current = tree_hashes(dist_dir)
    already_applied = current == candidate
    if not already_applied and current != baseline["files"]:
        return finish("중단: dist가 착수 기준도 후보도 아님(다른 변경이 있었음)", 1)

    plan = {"new": [], "replaced": [], "removed": []} if already_applied else make_plan(candidate, current)
    problems = [] if already_applied else plan_problems(plan)
    evidence.update(plan=plan, already_applied=already_applied, candidate_aggregate=manifest["aggregate_sha256"])
    print(f"계획: 새 파일 {len(plan['new'])} · 교체 {plan['replaced']} · 삭제 {len(plan['removed'])}"
          f"{' (이미 후보와 같은 상태)' if already_applied else ''}")
    if problems:
        return finish("중단: " + "; ".join(problems), 1)
    if not args.apply:
        return finish("드라이런 완료(변경 없음)", 0)

    api_before = api_summary(args.base) if args.base else None
    for relative in plan["new"]:                       # ① 새 asset
        copy_atomically(candidate_dir / relative, dist_dir / relative)
    for relative in plan["replaced"]:                  # ② index.html은 새 asset이 모두 놓인 뒤 교체
        copy_atomically(candidate_dir / relative, dist_dir / relative)
    for relative in plan["removed"]:                   # ③ 옛 asset은 마지막에 정리
        with_retries(lambda r=relative: (dist_dir / r).unlink(), f"삭제 {relative}")

    after = tree_hashes(dist_dir)
    tree_ok = after == candidate
    # 교체는 이미 끝났으므로, 검증 중 오류가 나도 증거를 남기고 실패로 보고한다.
    try:
        served = verify_served_bytes(args.base.rstrip("/"), candidate_dir, candidate) if args.base else None
        api_after = api_summary(args.base) if args.base else None
    except Exception as error:  # noqa: BLE001 -- 원인을 증거에 그대로 기록한다
        served, api_after = {"error": repr(error), "mismatches": [], "root_matches_index": False}, None
    served_ok = served is None or (not served["mismatches"] and served["root_matches_index"])
    api_same = api_before is None or (
        api_after is not None
        and api_before["runs"] == api_after["runs"]
        and api_before["attempt_count"] == api_after["attempt_count"]
    )
    ok = tree_ok and served_ok and api_same
    return finish("적용 완료" if ok else "적용 후 검증 불일치(증거 확인 필요)", 0 if ok else 1,
                  tree_matches_candidate=tree_ok, served=served, api_before=api_before, api_after=api_after,
                  api_runs_and_budget_unchanged=api_same)


if __name__ == "__main__":
    sys.exit(main())
