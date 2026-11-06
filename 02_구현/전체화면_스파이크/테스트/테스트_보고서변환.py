"""렌더러의 실제 바이트·오프라인 안전성 검사."""

from 서버.보고서변환 import render_report


async def test_직접렌더도_잘못된형식과_과대원문을_렌더전에_거부한다():
    from 서버.저장소 import ApiError

    for content, format, expected in [
        (b"small", "docx", "invalid_request"),
        (b"x" * 1048577, "html", "export_too_large"),
    ]:
        caught = None
        try:
            await render_report(content, format, {})
        except ApiError as error:
            caught = error
        assert caught is not None, (format, len(content))
        assert caught.code == expected


async def test_HTML은_본문인용출처를_보존하고_능동콘텐츠를_배제한다():
    markdown = """# 한글 보고서

본문 «문서명» 출처: CC-BY.

<script>alert(1)</script>

![외부 이미지](https://example.invalid/image.png)

[출처](https://example.invalid/source) [로컬](file:///C:/secret)

| 항목 | 값 |
| --- | --- |
| 한글 | 내용 |
""".encode()
    html = (await render_report(markdown, "html", {"title": "한글 <제목>"})).decode()
    assert "<h1>한글 보고서</h1>" in html
    assert "«문서명»" in html and "CC-BY" in html
    assert "<script" not in html and "<img" not in html
    assert 'href="file:' not in html and "src=" not in html
    assert 'href="https://example.invalid/source"' in html
    assert "<table>" in html
    assert "@page" in html and "Malgun Gothic" in html
    assert "한글 &lt;제목&gt;" in html


async def test_PDF시작중_취소도_Playwright_자식을_정리한다(monkeypatch):
    import asyncio
    import threading

    import pytest
    from playwright.async_api._context_manager import PlaywrightContextManager

    entered = threading.Event()
    managers = []
    original = PlaywrightContextManager.__aenter__

    async def delayed_start(manager):
        result = await original(manager)
        managers.append(manager)
        entered.set()
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(PlaywrightContextManager, "__aenter__", delayed_start)
    task = asyncio.create_task(render_report(b"# startup cancellation", "pdf", {}))
    for _ in range(100):
        if entered.is_set():
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert managers and managers[0]._connection._transport._stopped
    assert managers[0]._connection._transport._proc.returncode is not None
    assert not [t for t in threading.enumerate() if t.name == "report-pdf-renderer"]


async def test_PDF취소는_실제브라우저와_소유스레드가_정리된후에만_돌아온다(monkeypatch):
    import asyncio
    import threading

    import pytest
    from playwright.async_api import Page

    entered = threading.Event()
    captured = []

    async def held_pdf(page, **kwargs):
        captured.append(page.context.browser)
        entered.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(Page, "pdf", held_pdf)
    task = asyncio.create_task(render_report("# 취소 검사".encode(), "pdf", {}))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            if task.done():
                await task
                break
            # Windows 루프 clock_resolution(15.625ms)보다 길게 대기한다.
            await asyncio.sleep(0.05)
        assert entered.is_set(), repr(task)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert captured and not captured[0].is_connected()
    assert not [t for t in threading.enumerate() if t.name == "report-pdf-renderer"]


def test_Windows_Selector루프에서도_실제PDF를_생성한다():
    import asyncio

    loop = asyncio.SelectorEventLoop()
    try:
        pdf = loop.run_until_complete(render_report("# Selector 한글 검사".encode(), "pdf", {}))
        assert pdf.startswith(b"%PDF-")
    finally:
        loop.close()


async def test_실제_격리_Chromium_PDF는_한글인용출처와_여러페이지를_보존한다(tmp_path):
    import pypdfium2 as pdfium

    markdown = (
        "# 한글 변환 보고서\n\n"
        + "\n\n".join(
            f"## 절 {i}\n\n한글 본문과 내용입니다. «검증문서» 출처: CC-BY. " * 3 for i in range(40)
        )
    ).encode()
    pdf = await render_report(markdown, "pdf", {"title": "한글 변환 보고서"})
    assert pdf.startswith(b"%PDF-"), pdf[:80]
    (tmp_path / "긴문서.pdf").write_bytes(pdf)
    (tmp_path / "긴문서.html").write_bytes(
        await render_report(markdown, "html", {"title": "한글 변환 보고서"})
    )
    (tmp_path / "긴문서.md").write_bytes(markdown)
    with pdfium.PdfDocument(pdf) as document:
        assert len(document) >= 3
        texts = []
        for page in document:
            try:
                text = page.get_textpage()
                try:
                    texts.append(text.get_text_range())
                finally:
                    text.close()
            finally:
                page.close()
        full = "\n".join(texts)
        assert "한글 변환 보고서" in full
        assert "«검증문서»" in full and "CC-BY" in full
        assert "절 39" in full
