"""저장한 실제 Yahoo 응답(2026-11-07 진단 R1)을 FreeWebClient 검색 경로에 재생한다. 외부 요청 없음.

응답 HTML은 Git 밖 .cache/웹진단실측/에만 있으므로 이 검사는 그 파일이 있을 때만 실행된다.
"""

import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

BASE = Path(__file__).resolve().parents[1]  # 02_구현/전체화면_스파이크
ROOT = BASE.parents[1]  # 프로젝트 루트
sys.path.insert(0, str(BASE))

from 서버.웹자료 import FreeWebClient

SAVED = ROOT / ".cache/웹진단실측/r1_primp.html"
DIAGNOSTIC = BASE / "검증결과/웹진단실측_결과.json"
RESULT = BASE / "검증결과/웹응답재생_결과.json"
QUERY = "파이썬 asyncio 공식 문서"


async def replay(body):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=body)

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    client = FreeWebClient(search_http_client_factory=factory)
    try:
        return await client.search(QUERY), calls
    finally:
        await client.aclose()


def main():
    # 진단 스크립트는 write_text로 저장했으므로 Windows 줄바꿈 변환을 되돌려 읽어야 원문 해시와 비교된다.
    text = SAVED.read_text(encoding="utf-8")
    raw = text.encode()
    diagnostic = json.loads(DIAGNOSTIC.read_text(encoding="utf-8"))
    recorded = diagnostic["r1_primp"]["request"]["page"]
    response, calls = asyncio.run(replay(text))
    rows = response["results"]
    hosts = [urlsplit(row["url"]).hostname for row in rows]
    result = {
        "status": "REPLAY_PASS"
        if len(rows) >= 5 and "r.search.yahoo.com" not in hosts
        else "REPLAY_FAIL",
        "scope": "저장한 실제 응답 1건의 오프라인 재생. 실제 검색·모델·본문 요청 없음. 현재 응답이 같은 "
        "레이아웃이라는 보장이 아니라 저장한 응답에 대한 파서 동작 확인",
        "measured_at": datetime.now(UTC).isoformat(),
        "saved_response": {
            "path": ".cache/웹진단실측/r1_primp.html (Git 제외)",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "matches_diagnostic_record": hashlib.sha256(raw).hexdigest() == recorded["sha256"],
            "bytes": len(raw),
        },
        "before_fix": "ddgs 9.16.1 기본 파서: 같은 응답에서 No results found.(0건), 진단 기록 참조",
        "mock_requests": len(calls),
        "parsed_count": len(rows),
        "result_hosts": hosts,
        "unwrapped_urls": "r.search.yahoo.com" not in hosts,
        "results": [
            {
                "title": row["title"],
                "url": row["url"],
                "snippet_characters": len(row["snippet"]),
            }
            for row in rows
        ],
        "warnings": response.get("warnings"),
    }
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "parsed": len(rows),
                "hash_matches": result["saved_response"]["matches_diagnostic_record"],
                "unwrapped": result["unwrapped_urls"],
                "mock_requests": len(calls),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
