"""위임 범위의 진단 실측: 같은 질문의 Yahoo 응답 본문을 두 전송으로 저장한다.

R1은 예전 DDGS 기본 전송(primp), R2는 새 검색 경로와 같은 httpx 기본 요청이다.
검색 요청은 정확히 2회만 보내며 본문 수집·모델·앱 실행은 없다. 응답 HTML은 Git 밖 .cache에만 둔다.
R1에서 429·403·챌린지가 나오면 R2는 보내지 않는다.
`--check`는 전송 없이 준비 상태만 점검한다. 실제 실행은 배타 생성 표식으로 한 번만 허용한다.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from ddgs import DDGS
from ddgs.engines import ENGINES
from ddgs.engines.yahoo import Yahoo
from ddgs.exceptions import DDGSException
from ddgs.http_client import HttpClient
from lxml import html

BASE = Path(__file__).resolve().parents[1]  # 02_구현/전체화면_스파이크
ROOT = BASE.parents[1]  # 프로젝트 루트
QUERY = "파이썬 asyncio 공식 문서"
REGION, BACKEND = "kr-kr", "yahoo"  # 2026-11-06 무료대안검증과 같은 조건
ALLOWED_HOST = "search.yahoo.com"
GAP_SECONDS = 3
OUTPUT = ROOT / ".cache/웹진단실측"
MARKER = OUTPUT / "실행시작.json"
LOG = OUTPUT / "요청기록.jsonl"
RESULT = BASE / "검증결과/웹진단실측_결과.json"
BASELINE = BASE / "검증결과/웹재개_기준.json"
PROTECTED = ROOT / "03_실행데이터/실제연구"
DIST = BASE / "화면/dist"
SAFE_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "content-encoding",
    "server",
    "date",
    "cache-control",
    "retry-after",
    "location",
)
LOG_LOCK = threading.Lock()


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


def log(record):
    with LOG_LOCK, LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def inspect_page(text):
    """저장한 응답의 구조 표식만 센다. 본문 내용은 결과 JSON에 복사하지 않는다."""
    summary = {"bytes": len(text.encode()), "sha256": sha256_bytes(text.encode())}
    try:
        tree = html.fromstring(text)
    except (ValueError, html.etree.ParserError) as error:
        return {**summary, "parse_error": type(error).__name__}

    def count(xpath):
        return len(tree.xpath(xpath))

    lowered = text.lower()
    return {
        **summary,
        "title": " ".join(tree.xpath("//title/text()")).strip()[:160],
        "query_in_text": QUERY in text,
        "challenge_marker": any(
            marker in lowered for marker in ("captcha", "anomaly.js", "unusual traffic")
        ),
        "counts": {
            "div_relsrch": count("//div[contains(@class,'relsrch')]"),
            "section_algo": count("//section[contains(@class,'algo')]"),
            "any_algo": count("//*[contains(@class,'algo')]"),
            "h3": count("//h3"),
            "links": count("//a[@href]"),
        },
    }


def run_r1():
    """예전 DDGS 기본 전송. Python HTTP 경계의 요청을 1회로 제한하고 응답 본문을 저장한다."""
    original = HttpClient.request
    seen = []

    def observed(http_client, method, url, *args, **kwargs):
        record = {
            "route": "r1_primp",
            "method": method,
            "url": url,
            "params": kwargs.get("params"),
            "started_at": now(),
        }
        if urlsplit(url).hostname != ALLOWED_HOST or seen:
            record["guard"] = "refused"
            log(record)
            raise RuntimeError("probe_guard_refused")
        seen.append(record)
        record["guard"] = "allowed"
        began = time.perf_counter()
        try:
            response = original(http_client, method, url, *args, **kwargs)
        except Exception as error:
            # 실패 종류를 기록한 뒤 그대로 다시 올린다.
            record["error_type"] = type(error).__name__
            record["elapsed_ms"] = (time.perf_counter() - began) * 1000
            log(record)
            raise
        record.update(
            status_code=response.status_code,
            elapsed_ms=(time.perf_counter() - began) * 1000,
            page=inspect_page(response.text),
        )
        (OUTPUT / "r1_primp.html").write_text(response.text, encoding="utf-8")
        log(record)
        return response

    os.environ.pop(
        "DDGS_PROXY", None
    )  # 빈 값은 primp 초기화를 깨뜨린다(2026-11-06 실측과 같은 조건)
    DDGS.threads = 1
    HttpClient.request = lambda self, *args, **kwargs: observed(self, *args, **kwargs)
    try:
        with DDGS(timeout=12, verify=True) as engine:
            rows = engine.text(QUERY, region=REGION, max_results=5, backend=BACKEND)
        outcome = {"status": "ok" if rows else "empty", "result_count": len(rows)}
    except (DDGSException, ValueError) as error:
        outcome = {
            "status": "failed",
            "error_type": type(error).__name__,
            "message": str(error)[:160],
        }
    finally:
        HttpClient.request = original
    return {"request": seen[0] if seen else {}, "ddgs_outcome": outcome, "sent": len(seen)}


def run_r2():
    """새 검색 경로와 같은 httpx 기본 요청 1회. 응답 본문과 안전한 헤더만 남긴다."""
    engine = Yahoo(proxy=None, timeout=12, verify=True)
    payload = engine.build_payload(
        query=QUERY, region=REGION, safesearch="moderate", timelimit=None, page=1
    )
    record = {
        "route": "r2_httpx",
        "method": "GET",
        "url": engine.search_url,
        "params": payload,
        "started_at": now(),
        "guard": "allowed",
    }
    assert urlsplit(engine.search_url).hostname == ALLOWED_HOST
    began = time.perf_counter()
    with httpx.Client(timeout=12, verify=True, trust_env=False, follow_redirects=False) as client:
        try:
            response = client.get(engine.search_url, params=payload)
        except httpx.HTTPError as error:
            record["error_type"] = type(error).__name__
            record["elapsed_ms"] = (time.perf_counter() - began) * 1000
            log(record)
            return {"request": record, "sent": 1}
    text = response.text
    record.update(
        status_code=response.status_code,
        elapsed_ms=(time.perf_counter() - began) * 1000,
        request_headers={
            name: value
            for name, value in response.request.headers.items()
            if name.lower() in ("host", "accept", "accept-encoding", "connection", "user-agent")
        },
        response_header_names=sorted({name.lower() for name in response.headers}),
        response_headers={
            name: response.headers[name]
            for name in SAFE_RESPONSE_HEADERS
            if name in response.headers
        },
        page=inspect_page(text),
    )
    (OUTPUT / "r2_httpx.html").write_text(text, encoding="utf-8")
    log(record)
    return {"request": record, "sent": 1}


def check_only():
    engine_class = ENGINES.get("text", {}).get(BACKEND)
    ignored = (
        subprocess.run(
            ["git", "check-ignore", "-q", "--", ".cache/웹진단실측/r1_primp.html"],
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
                "query": QUERY,
                "search_request_limit": 2,
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
    r1 = run_r1()
    status = r1["request"].get("status_code")
    blocked = status in (403, 429) or r1["request"].get("page", {}).get("challenge_marker")
    if blocked:
        r2 = {"skipped": "R1에서 403·429·챌린지가 나와 R2는 보내지 않았다", "sent": 0}
    else:
        time.sleep(GAP_SECONDS)
        r2 = run_r2()
    sent = r1["sent"] + r2["sent"]
    result = {
        "status": "DIAGNOSTIC_CAPTURED" if sent == 2 else "DIAGNOSTIC_INCOMPLETE",
        "scope": "같은 질문의 Yahoo 응답 본문 저장 진단. 본문 수집·모델·앱 실행 없음. 원인 확정이 "
        "아니라 다음 보완의 근거 수집",
        "measured_at": now(),
        "conditions": {
            "r1": "예전 DDGS 기본 전송(primp): DDGS(timeout=12, verify=True).text(query, "
            f"region={REGION!r}, max_results=5, backend={BACKEND!r})",
            "r2": "새 검색 경로와 같은 httpx.Client(timeout=12, verify=True, trust_env=False, "
            "follow_redirects=False)의 GET, 헤더는 httpx 기본",
            "gap_seconds": GAP_SECONDS,
            "search_request_limit": 2,
        },
        "versions": {
            name: importlib.metadata.version(name) for name in ("ddgs", "primp", "httpx", "lxml")
        },
        "r1_primp": r1,
        "r2_httpx": r2,
        "request_counts": {"search_started": sent, "fetch_started": 0},
        "new_model_requests": 0,
        "paid_search_api_requests": 0,
        "automatic_retries": 0,
        "protected_matches_resume_baseline": protected_before
        == baseline["protected_runtime_sha256"],
        "protected_unchanged_by_probe": protected_before == tree_hashes(PROTECTED),
        "live_dist_unchanged_by_probe": dist_before == tree_hashes(DIST),
        "probe_script_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "raw_records": "프로젝트 .cache/웹진단실측/ (Git 제외, 응답 HTML 포함)",
    }
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "r1_http": status,
                "r1_ddgs": r1["ddgs_outcome"]["status"],
                "r2_http": r2.get("request", {}).get("status_code"),
                "r2_error": r2.get("request", {}).get("error_type"),
                "sent": sent,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
