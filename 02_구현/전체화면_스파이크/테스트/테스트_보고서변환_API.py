"""격리 합성 정본의 변환 HTTP 계약. 실제 제공자 호출 없음."""

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from 서버.앱 import create_app


@asynccontextmanager
async def export_client(path, renderer):
    app = create_app(data_dir=path, export_renderer=renderer)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        yield client, app


from 테스트.테스트_실행제어 import client_at, command, release, until


async def completed(client, app, request_id="export-test"):
    await command(client, "합성 변환 검사", request_id)
    active = await until(client, lambda s: len(s["waiting"]) == 4)
    await release(client, active["waiting"])
    done = await until(client, lambda s: s["runs"][-1]["status"] == "completed")
    await asyncio.gather(*list(app.state.service.tasks))
    return app.state.service.report_bytes(done["runs"][-1]["report_id"])[0]


async def test_미요청_조회는_생성없이_정본에_결합된다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        db = app.state.service.repo.path.read_bytes()
        response = await client.get(f"/api/spike/reports/{report['report_id']}/exports")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["report_id"] == report["report_id"]
        assert body["source_report_hash"] == report["sha256"]
        assert [e["format"] for e in body["exports"]] == ["html", "pdf"]
        for entry in body["exports"]:
            assert entry["status"] == "not_requested"
            assert all(
                entry[k] is None
                for k in ["created_at", "updated_at", "sha256", "download_url", "error"]
            )
        assert app.state.service.repo.path.read_bytes() == db
        assert not list((tmp_path / "형식변환").glob("*"))


@pytest.mark.parametrize("format", ["html", "pdf"])
async def test_새_변환파일명은_한국어이며_원본_Markdown의_이모지를_복사하지_않는다(
    tmp_path, format
):
    from urllib.parse import unquote

    async def filename_only_renderer(_markdown, _format, _report):
        # 파일명 HTTP 경계만 검사하는 명시적 대역이며 실제 PDF 검증을 대신하지 않는다.
        return b"filename-transport-fixture"

    async with export_client(tmp_path, filename_only_renderer) as (client, app):
        report = await completed(client, app)
        original_filename = app.state.service.report_bytes(report["report_id"])[2]
        url = f"/api/spike/reports/{report['report_id']}/exports"
        response = await client.post(
            url, json={"format": format, "source_report_hash": report["sha256"]}
        )
        assert response.status_code == 202
        ready = next(
            item for item in (await settled(client, report))["exports"] if item["format"] == format
        )
        response = await client.get(ready["download_url"])
        assert response.status_code == 200
        filename = unquote(
            response.headers["content-disposition"].partition("filename*=utf-8''")[2]
        )
        assert filename == f"연구보고서_{report['report_id']}.{format}"
        assert app.state.service.report_bytes(report["report_id"])[2] == original_filename


@pytest.mark.parametrize(
    "damage", ["bytes", "missing", "metadata", "url", "status_fields", "timestamps"]
)
async def test_손상된_파생파일은_다운로드를_막고_해당형식만_재시도한다(tmp_path, damage):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        url = f"/api/spike/reports/{report['report_id']}/exports"
        payload = {"format": "html", "source_report_hash": report["sha256"]}
        assert (await client.post(url, json=payload)).status_code == 202
        ready = (await settled(client, report))["exports"][0]
        before = app.state.service.repo.path.read_bytes()
        artifact = next((tmp_path / "형식변환").glob("*.html"))
        if damage == "bytes":
            artifact.write_bytes(b"tampered")
        elif damage == "missing":
            artifact.unlink()
        elif damage == "metadata":
            next((tmp_path / "형식변환").glob("*.json")).write_text("{broken", encoding="utf-8")
        else:
            import json

            path = next((tmp_path / "형식변환").glob("*.json"))
            value = json.loads(path.read_text(encoding="utf-8"))
            if damage == "url":
                value["download_url"] = "https://untrusted.invalid/secret"
            elif damage == "timestamps":
                value["updated_at"] = {"path": "PRIVATE_PATH"}
            else:
                value["error"] = {"code": "made_up", "message": "PRIVATE_PATH"}
            path.write_text(json.dumps(value), encoding="utf-8")
        rejected = await client.get(ready["download_url"])
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "export_integrity_error"
        failed = (await settled(client, report))["exports"][0]
        assert failed["status"] == "failed" and failed["error"]["code"] == "export_integrity_error"
        assert failed["sha256"] is None and failed["download_url"] is None
        assert (await client.post(url, json=payload)).status_code == 202
        retried = (await settled(client, report))["exports"]
        assert retried[0]["status"] == "ready" and retried[1]["status"] == "not_requested"
        assert (await client.get(retried[0]["download_url"])).status_code == 200
        assert app.state.service.repo.path.read_bytes() == before


async def settled(client, report):
    url = f"/api/spike/reports/{report['report_id']}/exports"
    for _ in range(500):
        body = (await client.get(url)).json()
        if all(e["status"] != "generating" for e in body["exports"]):
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(body)


@pytest.mark.parametrize("native_timeout", [False, True])
async def test_변환실패는_안전한사유로_보존하고_중복합치기와_명시재시도를_제공한다(
    tmp_path, native_timeout
):
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    from 서버.보고서변환 import render_report

    calls = []
    gate = asyncio.Event()

    async def renderer(markdown, format, report):
        calls.append((markdown, format, report))
        if len(calls) == 1:
            await gate.wait()
            error = PlaywrightTimeoutError if native_timeout else RuntimeError
            raise error("PRIVATE_C:/secret/token")
        return await render_report(markdown, format, report)

    async with export_client(tmp_path, renderer) as (client, app):
        report = await completed(client, app)
        before = app.state.service.repo.path.read_bytes()
        url = f"/api/spike/reports/{report['report_id']}/exports"
        payload = {"format": "html", "source_report_hash": report["sha256"]}
        first = await client.post(url, json=payload)
        duplicate = await client.post(url, json=payload)
        assert first.json() == duplicate.json()
        await asyncio.sleep(0)
        assert len(calls) == 1
        gate.set()
        await asyncio.gather(*list(app.state.exports.tasks.values()), return_exceptions=True)
        failed = (await client.get(url)).json()["exports"][0]
        assert failed["status"] == "failed", failed
        assert failed["error"]["code"] == ("export_timeout" if native_timeout else "export_failed")
        assert "PRIVATE" not in str(failed)
        assert failed["download_url"] is None and failed["sha256"] is None
        assert (await client.post(url, json=payload)).status_code == 202
        ready = (await settled(client, report))["exports"][0]
        assert ready["status"] == "ready" and len(calls) == 2
        assert calls[0] == calls[1] and calls[0][0] == report["markdown"].encode()
        assert (await client.post(url, json=payload)).json() == ready
        assert len(calls) == 2
        assert app.state.service.repo.path.read_bytes() == before


async def test_종료는_소유작업을_취소정리하고_재시작은_중단실패로_보존한다(tmp_path):
    import json

    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def renderer(markdown, format, report):
        entered.set()
        try:
            await asyncio.sleep(0.1)
            return b"unused"
        finally:
            cleaned.set()

    async with export_client(tmp_path, renderer) as (client, app):
        report = await completed(client, app)
        url = f"/api/spike/reports/{report['report_id']}/exports"
        payload = {"format": "html", "source_report_hash": report["sha256"]}
        await client.post(url, json=payload)
        await entered.wait()
        owned = list(app.state.exports.tasks.values())
    assert cleaned.is_set() and all(t.done() for t in owned)
    async with client_at(tmp_path) as (client, app):
        failed = (await client.get(url)).json()["exports"][0]
        assert failed["status"] == "failed", failed
        assert failed["error"]["code"] == "export_interrupted"
        assert not app.state.exports.tasks and not app.state.service.tasks
        # 프로세스 강제 종료로 generating JSON만 남은 경우도 동일하게 복원한다.
        path = next((tmp_path / "형식변환").glob("*.json"))
        failed.update(status="generating", error=None)
        path.write_text(json.dumps(failed), encoding="utf-8")
    async with client_at(tmp_path) as (client, app):
        failed = (await client.get(url)).json()["exports"][0]
        assert failed["status"] == "failed" and failed["error"]["code"] == "export_interrupted"
        assert (await client.post(url, json=payload)).status_code == 202
        assert (await settled(client, report))["exports"][0]["status"] == "ready"


async def test_변환제한시간은_정리완료후_timeout으로_보존한다(tmp_path, monkeypatch):
    import 서버.보고서변환 as module

    monkeypatch.setattr(module, "CONVERSION_TIMEOUT", 0.02, raising=False)
    cleaned = asyncio.Event()

    async def slow(markdown, format, report):
        try:
            await asyncio.sleep(0.1)
            return b"late"
        finally:
            cleaned.set()

    async with export_client(tmp_path, slow) as (client, app):
        report = await completed(client, app)
        url = f"/api/spike/reports/{report['report_id']}/exports"
        await client.post(url, json={"format": "html", "source_report_hash": report["sha256"]})
        final = (await settled(client, report))["exports"][0]
        assert final["status"] == "failed", final
        assert final["error"]["code"] == "export_timeout"
        await asyncio.gather(*list(app.state.exports.tasks.values()))
        await asyncio.sleep(0)  # 완료 callback의 등록부 정리를 한 번 양보한다.
        assert cleaned.is_set() and not app.state.exports.tasks
        assert not list((tmp_path / "형식변환").glob("*.html"))


async def test_PDF동시성은_1이며_다른보고서의_결과가_섞이지않는다(tmp_path):
    active = 0
    maximum = 0
    entered = []
    gate = asyncio.Event()

    async def renderer(markdown, format, report):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        entered.append(report["report_id"])
        try:
            await gate.wait()
            return ("%PDF-transport-test " + report["report_id"]).encode()
        finally:
            active -= 1

    async with export_client(tmp_path, renderer) as (client, app):
        reports = [await completed(client, app, request_id) for request_id in ("a", "b")]
        assert reports[0]["report_id"] != reports[1]["report_id"]
        for report in reports:
            url = f"/api/spike/reports/{report['report_id']}/exports"
            assert (
                await client.post(
                    url, json={"format": "pdf", "source_report_hash": report["sha256"]}
                )
            ).status_code == 202
        await asyncio.sleep(0.02)
        try:
            assert len(entered) == 1, entered
        finally:
            gate.set()
        for report in reports:
            entry = (await settled(client, report))["exports"][1]
            assert entry["status"] == "ready"
            download = await client.get(entry["download_url"])
            assert download.content == ("%PDF-transport-test " + report["report_id"]).encode()
            assert download.headers["content-type"] == "application/pdf"
        assert maximum == 1 and entered == [r["report_id"] for r in reports]


@pytest.mark.parametrize("size,status", [(1048576, 202), (1048577, 413)])
async def test_정본입력_1MiB_경계는_생성전_강제된다(tmp_path, size, status):
    import hashlib
    import json

    calls = []

    async def renderer(markdown, format, report):
        calls.append(markdown)
        return b"<html>size test</html>"

    async with export_client(tmp_path, renderer) as (client, app):
        report = await completed(client, app)
        # 이 테스트 전용 DB/정본을 일관되게 바꾸어 report_bytes 양성 경계를 통과한다.
        service = app.state.service
        _, _, filename = service.report_bytes(report["report_id"])
        content = b"x" * size
        report.update(markdown=content.decode(), sha256=hashlib.sha256(content).hexdigest())
        (tmp_path / filename).write_bytes(content)
        with service.repo.connection() as db:
            db.execute(
                "UPDATE reports SET body=? WHERE report_id=?",
                (json.dumps(report), report["report_id"]),
            )
            db.commit()
        assert service.report_bytes(report["report_id"])[1] == content
        before = service.repo.path.read_bytes()
        url = f"/api/spike/reports/{report['report_id']}/exports"
        response = await client.post(
            url, json={"format": "html", "source_report_hash": report["sha256"]}
        )
        assert response.status_code == status, response.text
        if status == 413:
            assert response.json()["error"]["code"] == "export_too_large"
            assert not calls and not app.state.exports.tasks
            assert not list((tmp_path / "형식변환").glob("*"))
        else:
            assert (await settled(client, report))["exports"][0]["status"] == "ready"
            assert calls == [content]
        assert service.repo.path.read_bytes() == before


async def test_저장실패도_경로노출없이_기존오류봉투로_반환한다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        (tmp_path / "형식변환").write_bytes(b"not a directory")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://127.0.0.1",
        ) as tolerant:
            response = await tolerant.post(
                f"/api/spike/reports/{report['report_id']}/exports",
                json={"format": "html", "source_report_hash": report["sha256"]},
            )
        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json"), response.text
        assert response.json() == {
            "error": {
                "code": "export_failed",
                "message": "변환 파일 상태를 저장할 수 없습니다. 다시 시도해 주세요.",
            }
        }
        assert not app.state.exports.tasks


async def test_실제ASGI_PDF생성다운로드도_기본렌더러를_통과한다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        before = app.state.service.repo.path.read_bytes()
        url = f"/api/spike/reports/{report['report_id']}/exports"
        response = await client.post(
            url, json={"format": "pdf", "source_report_hash": report["sha256"]}
        )
        assert response.status_code == 202
        entry = (await settled(client, report))["exports"][1]
        assert entry["status"] == "ready", entry
        download = await client.get(entry["download_url"])
        assert download.content.startswith(b"%PDF-")
        assert download.headers["content-type"] == "application/pdf"
        assert app.state.service.repo.path.read_bytes() == before


@pytest.mark.parametrize(
    "format,source_hash",
    [
        ("docx", "0" * 64),
        ("HTML", "0" * 64),
        ("html", "invalid"),
        ("html", "A" * 64),
        ("html", "0" * 63),
    ],
)
async def test_잘못된요청은_422_오류봉투이며_변환을_시작하지않는다(tmp_path, format, source_hash):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        response = await client.post(
            f"/api/spike/reports/{report['report_id']}/exports",
            json={"format": format, "source_report_hash": source_hash},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert not app.state.exports.tasks
        assert not list((tmp_path / "형식변환").glob("*"))


@pytest.mark.parametrize("target", ["status", "create", "download"])
async def test_정본무결성은_모든변환경로의_첫경계다(tmp_path, target):
    from 서버.보고서변환 import RENDERER_VERSION

    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        url = f"/api/spike/reports/{report['report_id']}/exports"
        assert (await client.get(url)).status_code == 200
        next(tmp_path.glob("*.md")).write_bytes(b"corrupt original")
        before = app.state.service.repo.path.read_bytes()
        if target == "create":
            response = await client.post(
                url, json={"format": "html", "source_report_hash": report["sha256"]}
            )
        elif target == "download":
            response = await client.get(
                url + "/html/download",
                params={
                    "source_report_hash": report["sha256"],
                    "renderer_version": RENDERER_VERSION,
                },
            )
        else:
            response = await client.get(url)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "report_integrity_error"
        assert not app.state.exports.tasks and app.state.service.repo.path.read_bytes() == before
        assert not list((tmp_path / "형식변환").glob("*"))


async def test_불합격초안과_없는보고서는_변환으로_제출하지않는다(tmp_path):
    from 서버.보고서변환 import RENDERER_VERSION

    async with client_at(tmp_path) as (client, app):
        await command(client, "합성 불합격", "blocked")
        active = await until(client, lambda s: len(s["waiting"]) == 4)
        await release(client, active["waiting"], "quality_fail")
        done = await until(client, lambda s: s["runs"][0]["status"] == "blocked")
        assert done["runs"][0]["draft"] and done["runs"][0]["report_id"] is None
        before = app.state.service.repo.path.read_bytes()
        url = "/api/spike/reports/no-final-report/exports"
        responses = [
            await client.get(url),
            await client.post(url, json={"format": "html", "source_report_hash": "0" * 64}),
            await client.get(
                url + "/html/download",
                params={"source_report_hash": "0" * 64, "renderer_version": RENDERER_VERSION},
            ),
        ]
        assert all(
            r.status_code == 404 and r.json()["error"]["code"] == "not_found" for r in responses
        )
        assert not app.state.exports.tasks and app.state.service.repo.path.read_bytes() == before
        assert not list(tmp_path.glob("*.md"))


async def test_다운로드는_검사한바이트를_반환하며_파일교체에_영향받지않는다(tmp_path, monkeypatch):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        url = f"/api/spike/reports/{report['report_id']}/exports"
        payload = {"format": "html", "source_report_hash": report["sha256"]}
        await client.post(url, json=payload)
        entry = (await settled(client, report))["exports"][0]
        original = app.state.exports.download
        artifact = next((tmp_path / "형식변환").glob("*.html"))
        expected = artifact.read_bytes()

        def replaced(*args):
            result = original(*args)
            artifact.write_bytes(b"replacement after verified read")
            return result

        monkeypatch.setattr(app.state.exports, "download", replaced)
        response = await client.get(entry["download_url"])
        assert response.status_code == 200 and response.content == expected
        assert response.content != artifact.read_bytes()


async def test_HTML생성_다운로드_재시작은_동일정본과_바이트를_보존한다(tmp_path):
    import hashlib
    from urllib.parse import unquote

    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        before = app.state.service.repo.path.read_bytes()
        url = f"/api/spike/reports/{report['report_id']}/exports"
        response = await client.post(
            url, json={"format": "html", "source_report_hash": report["sha256"]}
        )
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "generating"
        ready = (await settled(client, report))["exports"][0]
        assert ready["status"] == "ready", ready
        download = await client.get(ready["download_url"])
        assert download.status_code == 200, download.text
        assert "<h1>" in download.text and "«합성문서-1»" in download.text
        assert download.headers["content-type"].startswith("text/html")
        assert (
            download.headers["x-content-sha256"]
            == hashlib.sha256(download.content).hexdigest()
            == ready["sha256"]
        )
        assert download.headers["x-source-report-sha256"] == report["sha256"]
        assert unquote(download.headers["content-disposition"].split("''", 1)[1]).endswith(".html")
        assert app.state.service.repo.path.read_bytes() == before
    async with client_at(tmp_path) as (client, app):
        restored = (await settled(client, report))["exports"][0]
        assert restored == ready
        assert (await client.get(restored["download_url"])).content == download.content
        assert not app.state.service.tasks


@pytest.mark.parametrize("operation", ["post", "download_hash", "download_version"])
async def test_오래된_바인딩은_해시불일치로_거부하고_새작업이_없다(tmp_path, operation):
    from 서버.보고서변환 import RENDERER_VERSION

    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        url = f"/api/spike/reports/{report['report_id']}/exports"
        before = app.state.service.repo.path.read_bytes()
        if operation == "post":
            response = await client.post(
                url, json={"format": "html", "source_report_hash": "0" * 64}
            )
        else:
            response = await client.get(
                url + "/html/download",
                params={
                    "source_report_hash": "0" * 64
                    if operation == "download_hash"
                    else report["sha256"],
                    "renderer_version": "old"
                    if operation == "download_version"
                    else RENDERER_VERSION,
                },
            )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "report_hash_mismatch"
        assert not app.state.exports.tasks
        assert app.state.service.repo.path.read_bytes() == before
        assert not list((tmp_path / "형식변환").glob("*"))
