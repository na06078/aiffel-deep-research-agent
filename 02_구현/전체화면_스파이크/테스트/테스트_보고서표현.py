"""순수 표현 함수의 가시 내용/메타데이터 독립 기대값."""

import copy
from html.parser import HTMLParser

from 서버.보고서표현 import render_presentation

SVG = '<svg viewBox="0 0 360 120"><title>한글 자료 흐름</title><desc>수집에서 검토로 진행</desc><rect x="5" y="10" width="350" height="100" fill="#eef2ff"/><text x="20" y="60" font-size="20">수집 → 검토</text></svg>'
MARKDOWN = (
    "# 한글 보고서\n\n«문서1» 출처 CC-BY\n\n| 항목 | 값 |\n| --- | --- |\n| 한글 | 내용 |\n\n```svg\n"
    + SVG
    + "\n```\n\n[공식 **출처**](https://example.invalid/source)\n"
).encode()


class Document(HTMLParser):
    def __init__(self, value):
        super().__init__()
        self.tags = []
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_완전한_문서에_표_SVG_접근성_링크와_입력메타데이터를_보존한다():
    report = {"report_id": "보고서-1", "sha256": "a" * 64, "title": "검토 <제목>"}
    before = copy.deepcopy(report)
    result = render_presentation(MARKDOWN, report)
    assert set(result) == {
        "report_id",
        "source_report_hash",
        "renderer_version",
        "html",
        "warnings",
        "links",
    }
    assert result["report_id"] == "보고서-1" and result["source_report_hash"] == "a" * 64
    assert result["renderer_version"] == "markdown-it4-playwright1248-svg-v2"
    assert result["html"].startswith("<!doctype html>")
    assert "<table>" in result["html"] and "<td>한글</td>" in result["html"]
    assert "<svg " in result["html"] and "수집 → 검토" in result["html"]
    assert "검토 &lt;제목&gt;" in result["html"]
    tags = Document(result["html"]).tags
    assert next(attrs for tag, attrs in tags if tag == "svg")["role"] == "img"
    assert "한글 자료 흐름" in next(attrs for tag, attrs in tags if tag == "svg")["aria-label"]
    csp = next(
        attrs["content"]
        for tag, attrs in tags
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
    )
    assert "img-src 'none'" in csp and "default-src 'none'" in csp
    assert result["links"] == [{"text": "공식 출처", "url": "https://example.invalid/source"}]
    assert result["warnings"] == [] and report == before


def test_1MiB는_허용하고_초과는_파싱전_413으로_거부한다():
    import pytest

    from 서버.저장소 import ApiError

    assert "<p>" in render_presentation(b"x" * 1048576, {})["html"]
    with pytest.raises(ApiError) as caught:
        render_presentation(b"x" * 1048577, {})
    assert caught.value.status == 413 and caught.value.code == "export_too_large"


def test_악성SVG는_경고와_이스케이프코드로_원형보존한다():
    source = '<svg viewBox="0 0 10 10"><script>alert(1)</script></svg>'
    result = render_presentation(("```svg\n" + source + "\n```").encode(), {})
    tags = [tag for tag, _ in Document(result["html"]).tags]
    assert "svg" not in tags and "script" not in tags
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result["html"]
    assert len(result["warnings"]) == 1 and "도식을 표시할 수 없습니다" in result["warnings"][0]


def test_일반코드_HTML과_외부그림은_능동노드가_되지않는다():
    source = """<script>alert(1)</script>
<svg onload="alert(2)"></svg>

![외부 <그림>](https://example.invalid/image.png)

```python
print("실행 금지")
```

```html
<iframe src="https://example.invalid"></iframe>
```

```svg other
<svg viewBox="0 0 10 10"/>
```
"""
    result = render_presentation(source.encode(), {})
    tags = [tag for tag, _ in Document(result["html"]).tags]
    assert not {"svg", "script", "img", "iframe"}.intersection(tags)
    assert "외부 &lt;그림&gt;" in result["html"]
    assert "print(" in result["html"] and "&lt;iframe" in result["html"]
    assert result["links"] == [] and result["warnings"] == []


def test_허용http링크만_평문목록으로_반환하고_정본해시를_재계산하지않는다():
    source = """[https](https://example.invalid/a) [http](http://example.invalid/b)
[절](#section) [상대](/relative) [메일](mailto:abc@example.invalid)
[실행](javascript:alert(1)) [파일](file:///secret) [data](data:text/html,x)
[무호스트](http:///missing) [스킴상대](//example.invalid/a)
[https](https://example.invalid/a)
"""
    result = render_presentation(source.encode(), {"sha256": "supplied-binding"})
    assert result["source_report_hash"] == "supplied-binding"
    assert result["links"] == [
        {"text": "https", "url": "https://example.invalid/a"},
        {"text": "http", "url": "http://example.invalid/b"},
    ]
    anchors = [attrs["href"] for tag, attrs in Document(result["html"]).tags if tag == "a"]
    assert anchors == [
        "https://example.invalid/a",
        "http://example.invalid/b",
        "#section",
        "https://example.invalid/a",
    ]


def test_순수함수는_파일_프로세스_모델_네트워크를_사용하지않는다(monkeypatch):
    import builtins
    import pathlib
    import socket
    import subprocess

    def prohibited(*args, **kwargs):
        raise AssertionError("표현 함수의 I/O 금지")

    for target, name in [
        (builtins, "open"),
        (pathlib.Path, "open"),
        (socket, "socket"),
        (subprocess, "Popen"),
    ]:
        monkeypatch.setattr(target, name, prohibited)
    assert "수집 → 검토" in render_presentation(MARKDOWN, {})["html"]


def test_HTML내려받기는_순수표현과_동일한바이트를_사용한다():
    from 서버.보고서변환 import RENDERER_VERSION, report_html

    expected = render_presentation(MARKDOWN, {"title": "일치"})
    assert report_html(MARKDOWN, {"title": "일치"}) == expected["html"].encode()
    assert RENDERER_VERSION == "markdown-it4-playwright1248-svg-v2"


def test_본문_외부앵커는_빈_sandbox의_자체탐색을_허용하지않는다():
    result = render_presentation(
        b"[source](https://example.invalid/source) [section](#section)", {}
    )
    anchors = [attrs for tag, attrs in Document(result["html"]).tags if tag == "a"]
    assert anchors[0].get("target") == "_blank"
    assert anchors[0].get("rel") == "noopener noreferrer"
    assert "target" not in anchors[1]


def test_실제_빈_sandbox는_본문탐색을_막고_바깥출처만_별도탭으로_연다():
    """외부 호스트에는 전송하지 않고 Playwright route가 응답하는 탐색 경계 검사."""
    import html
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    url = "https://example.invalid/source"
    document = render_presentation(
        b"# canonical report\n\n[body source](https://example.invalid/source)", {}
    )["html"]
    requests = []
    denied = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(
                Path(__file__).resolve().parents[3]
                / ".cache/playwright/chromium-1248/chrome-win64/chrome.exe"
            ),
            headless=True,
        )
        try:
            context = browser.new_context()

            def route_request(route):
                if route.request.url == url:
                    requests.append(route.request.url)
                    route.fulfill(
                        status=200, content_type="text/html", body="<h1>memory-routed source</h1>"
                    )
                else:
                    denied.append(route.request.url)
                    route.abort()

            context.route("**/*", route_request)
            page = context.new_page()
            page.set_content(
                '<iframe title="report" sandbox="" srcdoc="'
                + html.escape(document, quote=True)
                + '"></iframe><a target="_blank" rel="noopener noreferrer" href="'
                + url
                + '">outside source</a>'
            )
            frame = page.frame_locator("iframe")
            frame.get_by_role("heading", name="canonical report", exact=True).wait_for()
            frame.get_by_role("link", name="body source", exact=True).click()
            page.wait_for_timeout(200)
            assert requests == [], "본문 앵커가 iframe 자체를 탐색했습니다."
            assert frame.get_by_role("heading", name="canonical report", exact=True).is_visible()
            with page.expect_popup() as opened:
                page.get_by_role("link", name="outside source", exact=True).click()
            opened.value.get_by_role("heading", name="memory-routed source", exact=True).wait_for()
            assert requests == [url]
            assert not denied
            context.close()
        finally:
            browser.close()
