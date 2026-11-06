"""승인된 무료 연결 실측: 검색4개·공개 본문 최대6페이지, 모델 호출 없음."""

import argparse
import hashlib
import ipaddress
import json
import os
import socket
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import trafilatura
from ddgs import DDGS
from ddgs.engines import ENGINES
from ddgs.exceptions import DDGSException
from ddgs.http_client import HttpClient

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parents[1] / "검증결과"
OUTPUT = ROOT / ".cache/웹검색_실측"
QUERIES = [
    ("검색_01", "파이썬 asyncio 공식 문서", "kr-kr", "duckduckgo"),
    ("검색_02", "공공데이터포털 OpenAPI 활용 가이드", "kr-kr", "brave"),
    ("검색_03", "Python asyncio documentation", "us-en", "duckduckgo"),
    ("검색_04", "LangGraph Send documentation", "us-en", "brave"),
]
PUBLIC_HOSTS = {
    "docs.python.org",
    "python.org",
    "www.python.org",
    "docs.langchain.com",
    "data.go.kr",
    "www.data.go.kr",
    "guide.ncloud-docs.com",
    "developer.mozilla.org",
    "en.wikipedia.org",
    "ko.wikipedia.org",
}


def now():
    return datetime.now(UTC).isoformat()


def append(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def ledger_digest():
    path = ROOT / "03_실행데이터/실제연구/호출예산.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        data = db.execute("SELECT rowid,* FROM attempts ORDER BY rowid").fetchall()
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def check_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in PUBLIC_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or {key.lower() for key in parse_qs(parsed.query)}
        & {"token", "key", "api_key", "auth", "signature", "session"}
    ):
        raise ValueError("public_probe_url_not_allowed")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("non_public_address")


def search_phase():
    path = OUTPUT / "검색기록.jsonl"
    if path.exists():
        raise ValueError("검색 기록이 이미 있습니다. 중복 실측을 시작하지 않습니다.")
    # 빈 문자열도 primp의 proxy URL로 넘어가므로 미설정 상태로 격리한다.
    os.environ.pop("DDGS_PROXY", None)
    DDGS.threads = 1
    original = HttpClient.request
    active = []

    def observed(self, method, url, *args, **kwargs):
        attempt = {"method": method, "url": url, "started_at": now()}
        active.append(attempt)
        try:
            result = original(self, method, url, *args, **kwargs)
            attempt["status"] = result.status_code
            attempt["challenge_marker"] = any(
                marker in result.text.lower()
                for marker in ("captcha", "anomaly.js", "unusual traffic")
            )
            return result
        except Exception as error:
            attempt["error_type"] = type(error).__name__
            raise
        finally:
            attempt["ended_at"] = now()

    HttpClient.request = observed
    try:
        for query_id, query, region, backend in QUERIES:
            if backend not in ENGINES["text"]:
                raise ValueError("명시한 검색 엔진이 비활성입니다. auto로 대체하지 않습니다.")
            active.clear()
            started = time.perf_counter()
            record = {"query_id": query_id, "query": query, "region": region, "backend": backend}
            try:
                with DDGS(timeout=12, verify=True) as engine:
                    results = engine.text(query, region=region, max_results=5, backend=backend)
                if not results or any(
                    not row.get("title") or not row.get("href") for row in results
                ):
                    raise ValueError("missing_search_results")
                record.update(status="ok", results=results)
            except (DDGSException, ValueError) as error:
                record.update(status="failed", error_type=type(error).__name__, results=[])
            record.update(elapsed_ms=(time.perf_counter() - started) * 1000, requests=list(active))
            append(path, record)
            print(
                json.dumps(
                    {key: record[key] for key in ("query_id", "backend", "status", "elapsed_ms")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            time.sleep(1)
    finally:
        HttpClient.request = original
    assert len(records(path)) == len(QUERIES)


def fetch_phase():
    queries = records(OUTPUT / "검색기록.jsonl")
    assert len(queries) == len(QUERIES)
    path = OUTPUT / "본문기록.jsonl"
    if path.exists():
        raise ValueError("본문 기록이 이미 있습니다. 중복 수집을 시작하지 않습니다.")
    selected, seen = [], set()
    for position in range(5):
        for query in queries:
            if position >= len(query["results"]):
                continue
            row = query["results"][position]
            url = row["href"]
            if url in seen or urlsplit(url).hostname not in PUBLIC_HOSTS:
                continue
            selected.append({"query_id": query["query_id"], **row})
            seen.add(url)
            if len(selected) == 6:
                break
        if len(selected) == 6:
            break
    with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
        for number, item in enumerate(selected, 1):
            record = {
                "source_id": f"자료_{number:02}",
                "url": item["href"],
                "query_id": item["query_id"],
                "search_title": item["title"],
                "fetched_at": now(),
            }
            started = time.perf_counter()
            try:
                url = item["href"]
                for _ in range(4):
                    check_url(url)
                    with client.stream(
                        "GET", url, headers={"User-Agent": "DeepResearch-Learning/0.1"}
                    ) as response:
                        record["http_status"] = response.status_code
                        if response.is_redirect:
                            url = urljoin(url, response.headers["location"])
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "")
                        if "html" not in content_type:
                            raise ValueError("not_html")
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 2 * 1024 * 1024:
                                raise ValueError("body_too_large")
                            chunks.append(chunk)
                        content = b"".join(chunks)
                    break
                else:
                    raise ValueError("redirect_limit")
                record.update(
                    final_url=url,
                    html_bytes=len(content),
                    html_sha256=hashlib.sha256(content).hexdigest(),
                )
                html_path = OUTPUT / f"본문_{number:02}.html"
                html_path.write_bytes(content)
                extracted = trafilatura.extract(
                    content,
                    url=url,
                    output_format="json",
                    with_metadata=True,
                    include_comments=False,
                )
                article = json.loads(extracted) if extracted else {}
                text = article.get("text") or ""
                record.update(
                    title=article.get("title"),
                    extracted_date_candidate=article.get("date"),
                    published_at=None,
                    date_verified=False,
                    text_characters=len(text),
                )
                if len(text.strip()) < 200:
                    raise ValueError("insufficient_extracted_text")
                (OUTPUT / f"본문_{number:02}.txt").write_text(text, encoding="utf-8")
                record.update(
                    status="ok",
                    text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    text_preview=text[:400],
                )
            except (httpx.HTTPError, ValueError, OSError) as error:
                record.update(
                    status="failed",
                    error_type=type(error).__name__,
                    reason=str(error) if isinstance(error, ValueError) else type(error).__name__,
                )
            record["elapsed_ms"] = (time.perf_counter() - started) * 1000
            append(path, record)
            print(
                json.dumps(
                    {
                        key: record.get(key)
                        for key in ("source_id", "url", "status", "text_characters", "reason")
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    if not selected:
        path.touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("search", "fetch"), required=True)
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((EVIDENCE / "웹검색_환경기준.json").read_text(encoding="utf-8"))
    assert ledger_digest() == baseline["budget_attempt_rows_sha256"]
    if args.phase == "search":
        search_phase()
    else:
        fetch_phase()
        queries, pages = records(OUTPUT / "검색기록.jsonl"), records(OUTPUT / "본문기록.jsonl")
        query_count, page_count = len(queries), len(pages)
        query_success = sum(row["status"] == "ok" for row in queries)
        page_success = sum(row["status"] == "ok" for row in pages)
        proof = {
            "status": "PASS" if query_success == 4 and page_success >= 3 else "PARTIAL",
            "query_count": query_count,
            "query_success": query_success,
            "page_count": page_count,
            "page_success": page_success,
            "queries": queries,
            "pages": pages,
            "new_model_requests": 0,
            "library_http_observation": "DDGS Python HTTP client request boundary; native redirects are not separately counted",
            "probe_scope": "네 검색어와 명시한 무료 두 엔진·허용한 공개 문서 도메인에서만 확인; 일반 웹 성공률 보장 아님",
            "installed_backend_caveat": "Bing text backend is disabled in9.16.1; auto also includes encyclopedia engines, so duckduckgo/brave are explicit",
            "measured_at": now(),
        }
        assert query_count == 4 and page_count <= 6
        (EVIDENCE / "웹검색_무료연결검증.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    key: proof[key]
                    for key in (
                        "status",
                        "query_count",
                        "query_success",
                        "page_count",
                        "page_success",
                        "new_model_requests",
                    )
                },
                ensure_ascii=False,
            )
        )
    assert ledger_digest() == baseline["budget_attempt_rows_sha256"]
    assert all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value
        for name, value in baseline["protected_sha256"].items()
    )


if __name__ == "__main__":
    main()
