"""승인된 새 FreeWebClient 최소 실측: 검색 1회 + 공개 본문 1건.

연구 앱·모델·예산 장부는 사용하지 않는다. `--check`는 전송 없이 준비 상태만 점검한다.
실제 실행은 배타 생성 표식으로 한 번만 허용하며 자동 재시도와 대체 경로는 없다.
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
PAGE = "https://docs.python.org/ko/3/library/asyncio.html"
# 연산별 허용 호스트와 Python HTTP 시작 상한. 본문은 같은 호스트의 redirect 1회까지만 1건으로 본다.
LIMITS = {"search": ("search.yahoo.com", 1), "fetch": ("docs.python.org", 2)}
MIN_TEXT_CHARACTERS = 200  # 기존 최초 실측의 insufficient_extracted_text 기준과 같다.
OUTPUT = ROOT / ".cache/웹최소실측"
MARKER = OUTPUT / "실행시작.json"
LOG = OUTPUT / "요청기록.jsonl"
SEARCH_DUMP = OUTPUT / "검색결과.json"
TEXT_DUMP = OUTPUT / "본문.txt"
RESULT = BASE / "검증결과/웹최소실측_결과.json"
PROTECTED = ROOT / "03_실행데이터/실제연구"
DIST = BASE / "화면/dist"
BASELINE = BASE / "검증결과/웹재개_기준.json"


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
        self.started = {"search": 0, "fetch": 0}
        self.events = []

    def __call__(self, kind, payload):
        operation = payload["operation"]
        host, limit = LIMITS[operation]
        record = {"kind": kind, **payload}
        with self.lock:
            if kind == "request.started":
                self.started[operation] += 1
                allowed = (
                    urlsplit(payload["url"]).hostname == host and self.started[operation] <= limit
                )
                record["guard"] = "allowed" if allowed else "refused"
            self.events.append(record)
            with LOG.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            if record.get("guard") == "refused":
                raise RuntimeError("probe_guard_refused")


async def run_search(client, guard):
    started = now()
    try:
        response = await client.search(QUERY, observer=guard)
    except WebSourceError as error:
        return {"status": "failed", "error_code": str(error), "started_at": started}
    except Exception as error:  # noqa: BLE001 -- 예기치 못한 실패도 종류만 증거로 남긴다.
        return {
            "status": "failed",
            "error_code": "unexpected_" + type(error).__name__,
            "started_at": started,
        }
    SEARCH_DUMP.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = response["results"]
    valid = bool(rows) and all(
        row["title"] and urlsplit(row["url"]).scheme in ("http", "https") for row in rows
    )
    return {
        "status": "ok" if valid else "empty_or_invalid",
        "started_at": started,
        "backend": response["backend"],
        "result_count": len(rows),
        "elapsed_ms": response["elapsed_ms"],
        "observed_requests": response["observed_requests"],
        "warnings": response.get("warnings", []),
        "exact_page_in_results": any(row["url"].rstrip("/") == PAGE for row in rows),
        "docs_python_hits": sum(urlsplit(row["url"]).hostname == "docs.python.org" for row in rows),
        "results": [
            {
                "title": row["title"],
                "url": row["url"],
                "snippet_characters": len(row["snippet"]),
            }
            for row in rows
        ],
    }


async def run_fetch(client, guard):
    started = now()
    try:
        page = await client.fetch(PAGE, observer=guard)
    except WebSourceError as error:
        return {"status": "failed", "error_code": str(error), "started_at": started}
    except Exception as error:  # noqa: BLE001
        return {
            "status": "failed",
            "error_code": "unexpected_" + type(error).__name__,
            "started_at": started,
        }
    text = page["text"]
    TEXT_DUMP.write_text(text, encoding="utf-8")
    relevant = "asyncio" in text.lower()
    korean = any("\uac00" <= char <= "\ud7a3" for char in text)
    enough = len(text.strip()) >= MIN_TEXT_CHARACTERS
    return {
        "status": "ok" if relevant and enough else "text_not_acceptable",
        "started_at": started,
        "requested_url": page["requested_url"],
        "final_url": page["final_url"],
        "title": page["title"],
        "text_characters": len(text),
        "contains_asyncio": relevant,
        "contains_korean": korean,
        "text_sha256": page["text_sha256"],
        "html_sha256": page["html_sha256"],
        "fetched_at": page["fetched_at"],
        "published_at": page["published_at"],
        "extracted_date_candidate": page["extracted_date_candidate"],
        "warnings": page["warnings"],
        "elapsed_ms": page["elapsed_ms"],
        "observed_requests": page["observed_requests"],
        "text_preview": text[:300],
    }


async def probe():
    guard = Guard()
    client = FreeWebClient()
    try:
        search = await run_search(client, guard)
        fetch = await run_fetch(client, guard)
    finally:
        await client.aclose()
    return guard, search, fetch


def check_only():
    from ddgs.ddgs import ENGINES

    yahoo = ENGINES.get("text", {}).get("yahoo")
    ignored = (
        subprocess.run(
            ["git", "check-ignore", "-q", "--", ".cache/웹최소실측/실행시작.json"],
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
                "output_inside_project": OUTPUT.resolve().is_relative_to(ROOT),
                "query": QUERY,
                "page": PAGE,
                "limits": LIMITS,
            },
            ensure_ascii=True,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="전송 없이 준비 상태만 점검")
    args = parser.parse_args()
    if args.check:
        check_only()
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    protected_before = tree_hashes(PROTECTED)
    dist_before = tree_hashes(DIST)
    try:
        with MARKER.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"started_at": now(), "query": QUERY, "page": PAGE}))
    except FileExistsError:
        raise SystemExit("실행 표식이 이미 있어 재실행하지 않습니다.") from None
    guard, search, fetch = asyncio.run(probe())
    passed = [search["status"] == "ok", fetch["status"] == "ok"]
    counts = {
        "search_started": guard.started["search"],
        "fetch_started": guard.started["fetch"],
        "refused": sum(event.get("guard") == "refused" for event in guard.events),
    }
    # redirect를 포함해 본문 호스트에서 마지막으로 완료된 요청의 상태를 따로 남긴다.
    completed = [e for e in guard.events if e["kind"] == "request.completed"]
    result = {
        "status": "PASS_LIMITED" if all(passed) else "PARTIAL" if any(passed) else "FAIL",
        "scope": "새 FreeWebClient 기본 구성의 검색1회·공개 본문1건. 일반 웹 가용성·본문 완전성·"
        "앱 종단 간·의미 품질 검증이 아님",
        "measured_at": now(),
        "client": "서버.웹자료.FreeWebClient() — 대역·주입 없음",
        "search": search,
        "fetch": fetch,
        "request_counts": counts,
        "http_completed": [
            {
                "operation": e["operation"],
                "status_code": e.get("status_code"),
                "elapsed_ms": e.get("elapsed_ms"),
                "url": e["url"],
            }
            for e in completed
        ],
        "http_failed": [
            {
                "operation": e["operation"],
                "error_code": e.get("error_code"),
                "status_code": e.get("status_code"),
                "retry_after_seconds": e.get("retry_after_seconds"),
            }
            for e in guard.events
            if e["kind"] == "request.failed"
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
        "raw_records": "프로젝트 .cache/웹최소실측/ (Git 제외)",
    }
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "search": search["status"],
                "search_error": search.get("error_code"),
                "fetch": fetch["status"],
                "fetch_error": fetch.get("error_code"),
                "counts": counts,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
