"""사본 정본·실제 HTML/PDF 렌더러로 삭제와 변환 저장의 경계를 검증한다.

대역 출력은 만들지 않는다. 실제 렌더링을 끝낸 다음 반환만 지연해 삭제와 경합시킨다.
실제 HTTP 소켓 대신 ASGITransport로 앱의 원래 API를 호출하며 모델·검색은 호출하지 않는다.
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pypdfium2
from 삭제_사본만들기 import COPIES, ROOT, make_copy

SPIKE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPIKE))
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")

from 서버.보고서변환 import RENDERER_VERSION
from 서버.연구앱 import create_research_app


async def check_format(format):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder = COPIES / f"{stamp}_실제변환_{format}"
    copied = make_copy(folder)
    ledger = folder / "호출예산.sqlite3"
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    app = create_research_app(data_dir=folder, ledger_path=ledger)
    async with app.router.lifespan_context(app):
        exports = app.state.exports
        snapshot = app.state.service.repo.snapshot()
        target = next(r for r in snapshot["runs"] if r.get("source_mode") == "web")
        keep = next(
            r
            for r in snapshot["runs"]
            if r.get("source_mode", "local") == "local" and r.get("report_id")
        )
        report = app.state.service.report_bytes(target["report_id"])[0]
        parent = app.state.service.report_bytes(keep["report_id"])[0]
        # 사본의 기존 파생 파일만 비워 실제 변환 작업을 새로 실행한다.
        purged = exports.purge({report["report_id"], parent["report_id"]})
        assert not purged["errors"], purged
        ready, release = asyncio.Event(), asyncio.Event()
        actual_renderer = exports.renderer
        measured = {}

        async def delayed(content, requested_format, metadata):
            rendered = await actual_renderer(content, requested_format, metadata)
            if metadata["report_id"] == report["report_id"]:
                measured.update(bytes=len(rendered), sha256=hashlib.sha256(rendered).hexdigest())
                if requested_format == "pdf":
                    assert rendered.startswith(b"%PDF-")
                else:
                    assert rendered.startswith(b"<!doctype html>")
                ready.set()
                await release.wait()
            return rendered

        exports.renderer = delayed
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8794"
        ) as client:
            response = await client.post(
                f"/api/spike/reports/{report['report_id']}/exports",
                json={"format": format, "source_report_hash": report["sha256"]},
            )
            assert response.status_code == 202, response.text
            task = exports.tasks[exports.key(report, format)]
            await asyncio.wait_for(ready.wait(), timeout=45)
            assert not task.done() and measured["bytes"] > 0
            deleted = await client.post(
                f"/api/spike/runs/{target['run_id']}/delete",
                json={
                    "confirm_run_id": target["run_id"],
                    "client_request_id": f"actual-render-delete-{format}",
                },
            )
            assert deleted.status_code == 200, deleted.text
            assert deleted.json()["warnings"] == []
            release.set()
            await asyncio.wait_for(task, timeout=10)
            paths = [exports.path(report, format, suffix) for suffix in (format, "json")]
            assert all(not path.exists() for path in paths), paths
            assert (
                await client.get(f"/api/spike/reports/{report['report_id']}")
            ).status_code == 404
            # 같은 실제 렌더러로 다른 보고서를 생성·다운로드·재열기하여 정상 기능 보존도 확인한다.
            kept = await client.post(
                f"/api/spike/reports/{parent['report_id']}/exports",
                json={"format": format, "source_report_hash": parent["sha256"]},
            )
            assert kept.status_code == 202, kept.text
            await asyncio.wait_for(exports.tasks[exports.key(parent, format)], timeout=45)
            response = await client.get(
                f"/api/spike/reports/{parent['report_id']}/exports/{format}/download",
                params={
                    "source_report_hash": parent["sha256"],
                    "renderer_version": RENDERER_VERSION,
                },
            )
            assert response.status_code == 200, response.text
            content = response.content
            stored = exports.path(parent, format, format)
            assert content == stored.read_bytes()
            assert hashlib.sha256(content).hexdigest() == response.headers["X-Content-SHA256"]
            pages = None
            if format == "pdf":
                with pypdfium2.PdfDocument(content) as document:
                    pages = len(document)
                    assert pages > 0
                    page = document[0]
                    try:
                        textpage = page.get_textpage()
                        try:
                            assert textpage.get_text_range().strip()
                        finally:
                            textpage.close()
                    finally:
                        page.close()
            else:
                assert '<html lang="ko">' in content.decode("utf-8")
        result = {
            "format": format,
            "status": "PASS",
            "copy": copied,
            "target_render": measured,
            "target_files_after": {p.name: p.exists() for p in paths},
            "preserved_export_bytes": len(content),
            "pdf_pages": pages,
            "ledger_unchanged": before == hashlib.sha256(ledger.read_bytes()).hexdigest(),
            "transport": "real ASGI API / actual renderer; renderer return delayed after actual conversion",
            "new_model_requests": 0,
            "new_search_requests": 0,
        }
        assert result["ledger_unchanged"]
        return result


async def main():
    output = SPIKE / "검증결과/삭제/실제변환_최종확인.json"
    results = []
    try:
        for format in ("html", "pdf"):
            result = await check_format(format)
            results.append(result)
            print(format, "PASS", result["target_render"], flush=True)
    finally:
        output.write_text(
            json.dumps(
                {
                    "checks": results,
                    "expected_formats": ["html", "pdf"],
                    "all_passed": len(results) == 2,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
