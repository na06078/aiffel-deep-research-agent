"""보완 후 검증 실측: 운영 기본 FreeWebClient()로 같은 질문 검색 1회만 보낸다.

위임 범위(진단 2회 + 보완 후 검증 1회) 중 마지막 1회다. 본문 수집·모델·앱 실행은 없다.
`--check`는 전송 없이 준비 상태만 점검한다. 실제 실행은 배타 생성 표식으로 한 번만 허용한다.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parents[1]  # 02_구현/전체화면_스파이크
ROOT = BASE.parents[1]  # 프로젝트 루트
sys.path.insert(0, str(BASE))

from 서버.웹계약 import WebSourceError
from 서버.웹자료 import FreeWebClient

QUERY = "파이썬 asyncio 공식 문서"
ALLOWED_HOST = "search.yahoo.com"
MAX_REQUESTS = 1
OUTPUT = ROOT / ".cache/웹보완검증실측"
MARKER = OUTPUT / "실행시작.json"
LOG = OUTPUT / "요청기록.jsonl"
RESULT = BASE / "검증결과/웹보완검증실측_결과.json"
BASELINE = BASE / "검증결과/웹재개_기준.json"
PROTECTED = ROOT / "03_실행데이터/실제연구"
DIST = BASE / "화면/dist"


def now():
    return datetime.now(UTC).isoformat()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def tree_hashes(directory):
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): sha256_bytes(path.read_bytes())
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class Guard:
    """전송 전에 호스트·횟수를 검사하고 모든 사건을 즉시 기록하는 observer."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = 0
        self.events = []

    def __call__(self, kind, payload):
        record = {"kind": kind, **payload}
        with self.lock:
            if kind == "request.started":
                self.started += 1
                allowed = (
                    urlsplit(payload["url"]).hostname == ALLOWED_HOST
                    and self.started <= MAX_REQUESTS
                )
                record["guard"] = "allowed" if allowed else "refused"
            self.events.append(record)
            with LOG.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            if record.get("guard") == "refused":
                raise RuntimeError("probe_guard_refused")


async def probe():
    guard = Guard()
    client = FreeWebClient()
    try:
        try:
            response = await client.search(QUERY, observer=guard)
        except WebSourceError as error:
            return guard, {"status": "failed", "error_code": str(error)}
        except Exception as error:  # noqa: BLE001 -- 예기치 못한 실패도 종류만 증거로 남긴다.
            return guard, {"status": "failed", "error_code": "unexpected_" + type(error).__name__}
    finally:
        await client.aclose()
    rows = response["results"]
    valid = bool(rows) and all(
        row["title"] and urlsplit(row["url"]).scheme in ("http", "https") for row in rows
    )
    hosts = [urlsplit(row["url"]).hostname for row in rows]
    return guard, {
        "status": "ok" if valid else "empty_or_invalid",
        "result_count": len(rows),
        "elapsed_ms": response["elapsed_ms"],
        "observed_requests": response["observed_requests"],
        "warnings": response.get("warnings", []),
        "unwrapped_urls": "r.search.yahoo.com" not in hosts,
        "docs_python_hits": sum(host == "docs.python.org" for host in hosts),
        "exact_page_in_results": any(
            row["url"].rstrip("/") == "https://docs.python.org/ko/3/library/asyncio.html"
            for row in rows
        ),
        "results": [
            {"title": row["title"], "url": row["url"], "snippet_characters": len(row["snippet"])}
            for row in rows
        ],
    }


def check_only():
    from ddgs.ddgs import ENGINES

    yahoo = ENGINES.get("text", {}).get("yahoo")
    ignored = (
        subprocess.run(
            ["git", "check-ignore", "-q", "--", ".cache/웹보완검증실측/실행시작.json"],
            cwd=ROOT,
            check=False,
        ).returncode
        == 0
    )
    print(
        json.dumps(
            {
                "mode": "check",
                "network": False,
                "marker_exists": MARKER.exists(),
                "yahoo_enabled": yahoo is not None and not yahoo.disabled,
                "output_ignored_by_git": ignored,
                "query": QUERY,
                "search_request_limit": MAX_REQUESTS,
            },
            ensure_ascii=True,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="전송 없이 준비 상태만 점검")
    if parser.parse_args().check:
        check_only()
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    protected_before = tree_hashes(PROTECTED)
    dist_before = tree_hashes(DIST)
    try:
        with MARKER.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"started_at": now(), "query": QUERY}))
    except FileExistsError:
        raise SystemExit("실행 표식이 이미 있어 재실행하지 않습니다.") from None
    guard, search = asyncio.run(probe())
    result = {
        "status": "PASS_LIMITED" if search["status"] == "ok" else "FAIL",
        "scope": "운영 기본 FreeWebClient()의 검색1회. 본문 수집·모델·앱 실행 없음. 일반 웹 가용성·"
        "지속 안정성·앱 종단 간 검증이 아님",
        "measured_at": now(),
        "client": "서버.웹자료.FreeWebClient() — 대역·주입 없음(기본 전송 primp)",
        "search": search,
        "request_counts": {
            "search_started": guard.started,
            "refused": sum(event.get("guard") == "refused" for event in guard.events),
            "fetch_started": 0,
        },
        "http_events": [
            {
                key: event.get(key)
                for key in ("kind", "status_code", "elapsed_ms", "error_code", "guard")
            }
            for event in guard.events
        ],
        "new_model_requests": 0,
        "paid_search_api_requests": 0,
        "automatic_retries": 0,
        "protected_matches_resume_baseline": protected_before
        == baseline["protected_runtime_sha256"],
        "protected_unchanged_by_probe": protected_before == tree_hashes(PROTECTED),
        "live_dist_unchanged_by_probe": dist_before == tree_hashes(DIST),
        "probe_script_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "client_source_sha256": sha256_bytes((BASE / "서버/웹자료.py").read_bytes()),
        "parser_source_sha256": sha256_bytes((BASE / "서버/야후결과파서.py").read_bytes()),
        "raw_records": "프로젝트 .cache/웹보완검증실측/ (Git 제외)",
    }
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "search": search["status"],
                "error": search.get("error_code"),
                "results": search.get("result_count"),
                "counts": result["request_counts"],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
