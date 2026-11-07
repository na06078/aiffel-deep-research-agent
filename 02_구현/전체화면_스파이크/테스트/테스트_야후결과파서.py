"""Yahoo section.s-algo 레이아웃 파서와 FreeWebClient 연결. 외부 요청 없음.

FIXTURE는 2026-11-07 실제 응답의 구조(클래스·중첩)만 본뜬 합성 HTML이며 제3자 문구를 복사하지 않는다.
"""

import asyncio
from types import SimpleNamespace

import httpcore
import httpx
import pytest
from lxml import html

from 서버.야후결과파서 import looks_like_results, parse_algo_results
from 서버.웹계약 import WebSourceError
from 서버.웹자료 import FreeWebClient


@pytest.fixture(autouse=True)
def forbid_default_network(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    async def async_denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", async_denied)


def item(number, title, wrapped, description, nested=""):
    """결과 하나의 실제 구조: 제목은 h3/a의 직접 텍스트, 사이트명·주소는 a 안의 span, 설명은 ext 안."""
    desc = (
        f'<div class="compText"><p class="s-desc lh-20">{description}</p></div>'
        if description
        else ""
    )
    return f"""
<section class="dd algo s-algo algo-sr Sr">
  <div class="grp grp-talgo ag-{number} u-tapHgt cur-p">
    <div class="compTitle p-r"><h3 class="title d-ib mt-42">
      <a class="s-title fz-m" href="https://r.search.yahoo.com/_ylt=A;_ylu=B/RV=2/RE=1/RO=10/RU={wrapped}/RK=2/RS=x">
        <span class="d-ib titl"><span>예시사이트</span><span>https://example.com › 경로</span></span>{title}
      </a></h3></div>
  </div>
  <div class="grp grp-talgo-ext u-negmlrEdges">{desc}{nested}</div>
</section>"""


# 실제 응답처럼 다음 결과가 앞 결과의 ext 영역 안에 중첩되고, 결과가 아닌 동영상 묶음 section도 섞여 있다.
THIRD = item(3, "세 번째 예시 제목", "https%3a%2f%2fexample.com%2fthird", "")
SECOND = item(
    2, "두 번째 예시 제목", "https%3a%2f%2fexample.com%2fsecond", "두 번째 설명입니다.", THIRD
)
VIDEO = (
    '<section class="mi6 s-dd Video uds uds-dd"><h3>동영상 묶음 제목은 결과가 아니다</h3></section>'
)
FIRST = item(
    1,
    "첫 번째 예시 제목",
    "https%3a%2f%2fexample.com%2ffirst",
    "<b>예시</b> 첫 번째 설명입니다.",
    VIDEO + SECOND,
)
FIXTURE = (
    "<html><head><title>예시 질문 - Yahoo Search Results</title></head><body>"
    '<section class="reg searchCenterMiddle"><section id="algo-bd"><section id="main-algo">'
    f'<div id="layout">{FIRST}</div></section></section></section></body></html>'
)


def test_중첩된_algo_결과를_섞지않고_직계_경로로_제목_링크_설명을_읽는다():
    rows = parse_algo_results(html.fromstring(FIXTURE))
    assert [row["title"] for row in rows] == [
        "첫 번째 예시 제목",
        "두 번째 예시 제목",
        "세 번째 예시 제목",
    ]
    assert [row["body"] for row in rows] == ["예시 첫 번째 설명입니다.", "두 번째 설명입니다.", ""]
    assert all(row["href"].startswith("https://r.search.yahoo.com/") for row in rows)
    assert all("동영상 묶음" not in row["title"] for row in rows)


def test_결과_구조_표식_판정은_알려진_레이아웃과_h3_밀도를_본다():
    def page(body):
        return html.fromstring(f"<html><body>{body}</body></html>")

    assert looks_like_results(html.fromstring(FIXTURE))
    assert looks_like_results(page('<div class="relsrch"></div>'))
    assert looks_like_results(page("<h3>a</h3><h3>b</h3><h3>c</h3>"))  # 알려지지 않은 새 레이아웃
    assert not looks_like_results(page("<p>No results</p>"))
    assert not looks_like_results(page("<h3>a</h3><h3>b</h3>"))


def client_for(body):
    def handler(request):
        return httpx.Response(200, text=body)

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return FreeWebClient(search_http_client_factory=factory)


def test_새_레이아웃_응답이_FreeWebClient_검색_결과가_된다():
    async def scenario():
        client = client_for(FIXTURE)
        try:
            return await client.search("예시 질문")
        finally:
            await client.aclose()

    result = asyncio.run(scenario())
    assert sorted(row["url"] for row in result["results"]) == [
        "https://example.com/first",
        "https://example.com/second",
        "https://example.com/third",
    ]
    assert {row["title"] for row in result["results"]} == {
        "첫 번째 예시 제목",
        "두 번째 예시 제목",
        "세 번째 예시 제목",
    }


def test_알려진_구조_표식이_있는데_0건이면_빈_결과가_아니라_search_unparsed():
    changed = (
        '<html><body><section class="s-algo"><div>구조가 바뀐 결과</div></section></body></html>'
    )

    async def scenario():
        client, events = client_for(changed), []
        try:
            with pytest.raises(WebSourceError, match="^search_unparsed$"):
                await client.search("질문", observer=lambda kind, payload: events.append(kind))
        finally:
            await client.aclose()
        return events

    # HTTP 요청 자체는 200으로 끝났으므로 request.failed 없이 started/completed만 남는다.
    assert asyncio.run(scenario()) == ["request.started", "request.completed"]


def test_결과_구조_표식이_없는_빈_페이지는_기존처럼_빈_결과():
    async def scenario():
        client = client_for("<html><body>No results</body></html>")
        try:
            return await client.search("질문")
        finally:
            await client.aclose()

    assert asyncio.run(scenario())["results"] == []


class NativeClient:
    """DDGS 기본 전송의 대역. 기본 검색 경로가 이 객체를 교체하면 안 된다."""


def test_기본_검색_전송은_DDGS_기본_클라이언트를_교체하지_않는다():
    native, seen = NativeClient(), []

    class Engine:
        name, disabled = "yahoo", False

        def __init__(self):
            self.http_client = SimpleNamespace(client=native, request=self.request)

        def request(self, method, url, *args, **kwargs):
            seen.append((method, url, kwargs.get("params")))
            page = "<html></html>"
            return SimpleNamespace(
                status_code=200, content=page.encode(), text=page, _resp=SimpleNamespace(headers={})
            )

        def extract_results(self, text):
            return []

    engine = Engine()

    class Sdk:
        def __init__(self, **kwargs):
            pass

        def _get_engines(self, category, backend):
            return [engine]

        def text(self, query, **kwargs):
            engine.http_client.request(
                "GET", "https://search.yahoo.com/search", params={"p": query}
            )
            return [{"title": "제목", "href": "https://example.com/", "body": "설명"}]

    async def scenario():
        client = FreeWebClient(ddgs_factory=Sdk)
        try:
            return await client.search("질문")
        finally:
            await client.aclose()

    result = asyncio.run(scenario())
    assert engine.http_client.client is native
    assert seen == [("GET", "https://search.yahoo.com/search", {"p": "질문"})]
    assert result["results"][0]["url"] == "https://example.com/"
