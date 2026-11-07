"""승인된 대조 실측: 예전 DDGS 기본 전송(primp)으로 같은 질문 검색 1회.

새 FreeWebClient가 받은 Yahoo HTTP 500의 원인을 가르기 위한 대조군이다.
코드 변경·본문 수집·모델 호출은 없다. `--check`는 전송 없이 준비 상태만 점검한다.
실제 실행은 배타 생성 표식으로 한 번만 허용하며 자동 재시도와 대체 경로는 없다.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ddgs import DDGS
from ddgs.engines import ENGINES
from ddgs.exceptions import DDGSException
from ddgs.http_client import HttpClient

BASE = Path(__file__).resolve().parents[1]  # 02_구현/전체화면_스파이크
ROOT = BASE.parents[1]  # 프로젝트 루트
QUERY = "파이썬 asyncio 공식 문서"
REGION, BACKEND = "kr-kr", "yahoo"  # 2026-11-06 무료대안검증과 같은 조건
ALLOWED_HOST, MAX_REQUESTS = "search.yahoo.com", 1
OUTPUT = ROOT / ".cache/웹대조실측"
MARKER = OUTPUT / "실행시작.json"
LOG = OUTPUT / "요청기록.jsonl"
RESULT = BASE / "검증결과/웹대조실측_결과.json"
NEW_RESULT = BASE / "검증결과/웹최소실측_결과.json"
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


class Observed:
    """Python HTTP 경계의 요청을 전송 전에 검사하고 즉시 기록한다. primp 내부 redirect는 볼 수 없다."""

    def __init__(self, original):
        self.original = original
        self.lock = threading.Lock()
        self.started = 0
        self.records = []

    def save(self, record):
        with self.lock:
            self.records.append(record)
            with LOG.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def __call__(self, http_client, method, url, *args, **kwargs):
        with self.lock:
            self.started += 1
            attempt = self.started
        record = {
            "attempt": attempt,
            "method": method,
            "url": url,
            "params": kwargs.get("params"),
            "started_at": now(),
        }
        if urlsplit(url).hostname != ALLOWED_HOST or attempt > MAX_REQUESTS:
            record["guard"] = "refused"
            self.save(record)
            raise RuntimeError("probe_guard_refused")
        record["guard"] = "allowed"
        # primp가 제공하면 이번 요청에 쓴 브라우저 프로필을 남긴다(없으면 None).
        record["impersonate"] = str(getattr(http_client.client, "impersonate", None))
        record["impersonate_os"] = str(getattr(http_client.client, "impersonate_os", None))
        began = time.perf_counter()
        try:
            response = self.original(http_client, method, url, *args, **kwargs)
        except Exception as error:
            # 실패 종류를 기록한 뒤 그대로 다시 올린다.
            record["error_type"] = type(error).__name__
            record["elapsed_ms"] = (time.perf_counter() - began) * 1000
            self.save(record)
            raise
        text = response.text.lower()
        title = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.IGNORECASE | re.DOTALL)
        record.update(
            status_code=response.status_code,
            elapsed_ms=(time.perf_counter() - began) * 1000,
            body_bytes=len(response.content),
            title=(title.group(1).strip()[:120] if title else None),
            has_result_markup="relsrch" in text,
            challenge_marker=any(
                marker in text for marker in ("captcha", "anomaly.js", "unusual traffic")
            ),
        )
        self.save(record)
        return response


def check_only():
    engine_class = ENGINES.get("text", {}).get(BACKEND)
    ignored = (
        subprocess.run(
            ["git", "check-ignore", "-q", "--", ".cache/웹대조실측/실행시작.json"],
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
                "yahoo_enabled": engine_class is not None and not engine_class.disabled,
                "output_ignored_by_git": ignored,
                "new_probe_result_exists": NEW_RESULT.is_file(),
                "query": QUERY,
                "region": REGION,
                "backend": BACKEND,
                "max_requests": MAX_REQUESTS,
            },
            ensure_ascii=True,
        )
    )


def interpret(control_ok, control_status, new_status):
    if control_ok:
        return (
            f"같은 PC·네트워크·질문에서 DDGS 기본 전송(primp)은 HTTP {control_status}와 결과를 받았고 "
            f"새 httpx 경로는 HTTP {new_status}를 받았다. 새 경로의 전송 설정(헤더·TLS 지문 등)이 "
            "원인 후보로 좁혀지지만 단일 관측이고 두 요청 사이에 시간차가 있어 최종 원인은 확정하지 않는다."
        )
    return (
        f"대조군도 거절 또는 실패했다(HTTP {control_status}). 새 경로 고유의 원인보다 Yahoo 쪽 "
        "일시 오류·차단 가능성이 커졌지만 단일 관측이라 확정하지 않는다."
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="전송 없이 준비 상태만 점검")
    if parser.parse_args().check:
        check_only()
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    new_probe = json.loads(NEW_RESULT.read_text(encoding="utf-8"))
    protected_before = tree_hashes(PROTECTED)
    dist_before = tree_hashes(DIST)
    try:
        with MARKER.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"started_at": now(), "query": QUERY}))
    except FileExistsError:
        raise SystemExit("실행 표식이 이미 있어 재실행하지 않습니다.") from None
    # 2026-11-06 무료대안검증과 같은 조건: 빈 DDGS_PROXY는 primp 초기화를 깨뜨리므로 제거한다.
    os.environ.pop("DDGS_PROXY", None)
    DDGS.threads = 1
    original = HttpClient.request
    observed = Observed(original)
    HttpClient.request = lambda self, *args, **kwargs: observed(self, *args, **kwargs)
    started = now()
    try:
        with DDGS(timeout=12, verify=True) as engine:
            rows = engine.text(QUERY, region=REGION, max_results=5, backend=BACKEND)
        valid = bool(rows) and all(row.get("title") and row.get("href") for row in rows)
        outcome = {
            "status": "ok" if valid else "empty_or_invalid",
            "result_count": len(rows),
            "results": [
                {
                    "title": row.get("title"),
                    "url": row.get("href"),
                    "snippet_characters": len(row.get("body", "")),
                }
                for row in rows
            ],
        }
    except (DDGSException, ValueError) as error:
        outcome = {
            "status": "failed",
            "error_type": type(error).__name__,
            "message": str(error)[:160],
        }
    finally:
        HttpClient.request = original
    request = observed.records[0] if observed.records else {}
    control_ok = outcome["status"] == "ok" and request.get("status_code") == 200
    new_status = next(
        (item["status_code"] for item in new_probe["http_failed"] if item["operation"] == "search"),
        None,
    )
    result = {
        "status": "CONTROL_ACCEPTED" if control_ok else "CONTROL_REJECTED",
        "scope": "예전 DDGS 기본 전송(primp)으로 같은 질문 검색 1회. 본문 수집·모델·앱 실행 없음. "
        "일반 웹 가용성·원인 확정 검증이 아님",
        "measured_at": now(),
        "started_at": started,
        "conditions": {
            "call": f"DDGS(timeout=12, verify=True).text(query, region={REGION!r}, max_results=5, backend={BACKEND!r})",
            "same_as": "검증결과/웹검색_무료대안검증.json의 Yahoo 검색 조건",
            "ddgs_threads": 1,
            "ddgs_proxy_env": "제거",
            "request_limit": MAX_REQUESTS,
        },
        "versions": {
            name: importlib.metadata.version(name) for name in ("ddgs", "primp", "httpx", "lxml")
        },
        "request": request,
        "outcome": outcome,
        "comparison": {
            "new_client_http_status": new_status,
            "new_client_measured_at": new_probe["measured_at"],
            "control_http_status": request.get("status_code"),
            "minutes_between": round(
                (
                    datetime.fromisoformat(now()) - datetime.fromisoformat(new_probe["measured_at"])
                ).total_seconds()
                / 60,
                1,
            ),
            "interpretation": interpret(control_ok, request.get("status_code"), new_status),
        },
        "observation_limits": "Python HTTP 경계의 요청만 센다. primp 내부 redirect·TLS 협상은 관측하지 못한다.",
        "request_counts": {
            "search_started": observed.started,
            "refused": sum(r.get("guard") == "refused" for r in observed.records),
            "fetch_started": 0,
        },
        "new_model_requests": 0,
        "paid_search_api_requests": 0,
        "automatic_retries": 0,
        "protected_matches_resume_baseline": protected_before
        == baseline["protected_runtime_sha256"],
        "protected_unchanged_by_probe": protected_before == tree_hashes(PROTECTED),
        "live_dist_unchanged_by_probe": dist_before == tree_hashes(DIST),
        "probe_script_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "raw_records": "프로젝트 .cache/웹대조실측/ (Git 제외)",
    }
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "http_status": request.get("status_code"),
                "outcome": outcome["status"],
                "results": outcome.get("result_count"),
                "counts": result["request_counts"],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
