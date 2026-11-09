"""확정 정본의 요청형 파생 파일. 연구 저장소에는 쓰지 않는다."""

import asyncio
import copy
import hashlib
import json
import re
import sys
import threading
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from 서버.보고서표현 import MAX_SOURCE_BYTES, RENDERER_VERSION, render_presentation
from 서버.저장소 import ApiError, now


def report_html(markdown, report):
    return render_presentation(markdown, report)["html"].encode("utf-8")


async def render_report(markdown: bytes, format: str, report: dict) -> bytes:
    if format not in {"html", "pdf"}:
        raise ApiError(422, "invalid_request", "HTML 또는 PDF 형식을 선택해 주세요.")
    if len(markdown) > MAX_SOURCE_BYTES:
        raise ApiError(413, "export_too_large", "변환 원문은 1MiB 이하여야 합니다.")
    content = report_html(markdown, report)
    if format == "html":
        return content
    return await render_pdf(content)


async def browser_pdf(content):
    from playwright.async_api import async_playwright

    root = Path(__file__).resolve().parents[3]
    executable = root / ".cache/playwright/chromium-1248/chrome-win64/chrome.exe"
    # __aenter__/launch가 자원을 연 뒤 반환하기 전 취소되는 경합도 소유한다.
    start = asyncio.create_task(async_playwright().start())
    playwright = None
    launching = None
    browser = None
    try:
        playwright = await asyncio.shield(start)
        launching = asyncio.create_task(
            playwright.chromium.launch(
                executable_path=str(executable),
                headless=True,
                timeout=15000,
                args=["--disable-background-networking", "--disable-component-update"],
            )
        )
        browser = await asyncio.shield(launching)
        context = await browser.new_context(
            java_script_enabled=False,
            service_workers="block",
            offline=True,
        )
        await context.route("**/*", lambda route: route.abort())
        page = await context.new_page()
        await page.set_content(content.decode("utf-8"), wait_until="load", timeout=15000)
        await page.emulate_media(media="print")
        return await page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
    finally:
        if playwright is None:
            playwright = await start
        try:
            if launching is not None:
                if browser is None:
                    browser = await launching
                await browser.close()
        finally:
            await playwright.stop()


async def render_pdf(content):
    """Windows ASGI Selector에서도 자식 실행 가능한 소유 Proactor를 쓴다.

    취소 시 작업을 취소한 뒤 Playwright finally와 루프/스레드 종료까지 기다린다.
    fire-and-forget to_thread가 아니므로 timeout 뒤 브라우저를 남기지 않는다.
    """
    owner_loop = asyncio.get_running_loop()
    done = asyncio.Event()
    cancelled = threading.Event()
    state = {}

    def worker():
        loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(browser_pdf(content))
        state.update(loop=loop, task=task)
        if cancelled.is_set():
            task.cancel()
        try:
            state["result"] = loop.run_until_complete(task)
        except BaseException as error:  # noqa: BLE001 -- 취소도 소유 루프에서 호출자에게 전달한다.
            state["error"] = error
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            owner_loop.call_soon_threadsafe(done.set)

    thread = threading.Thread(target=worker, name="report-pdf-renderer", daemon=False)
    thread.start()

    async def joined():
        await done.wait()
        thread.join()

    try:
        await joined()
    except asyncio.CancelledError:
        cancelled.set()
        loop, task = state.get("loop"), state.get("task")
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass  # 루프가 이미 정리된 경합도 joined로 확인한다.
        cleanup = asyncio.create_task(joined())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        raise
    if "error" in state:
        raise state["error"]
    return state["result"]


CONVERSION_TIMEOUT = 20
SAFE_ERRORS = {
    "export_failed": "형식 변환에 실패했습니다. 다시 시도해 주세요.",
    "export_timeout": "변환 제한시간을 초과했습니다. 다시 시도해 주세요.",
    "export_interrupted": "앱 종료 또는 재시작으로 변환이 중단되었습니다. 다시 시도해 주세요.",
    "export_integrity_error": "변환 파일 또는 상태가 손상되었습니다. 다시 생성해 주세요.",
}


class ReportExports:
    def __init__(self, service, renderer=None):
        self.service = service
        self.renderer = renderer or render_report
        self.tasks = {}
        self.pdf_slot = asyncio.Semaphore(1)

    @property
    def root(self):
        # 미요청 수명/health는 저장소에 접근하지 않는다. 실제 변환 때만 바인딩한다.
        return self.service.repo.root / "형식변환"

    def key(self, report, format):
        return hashlib.sha256(
            json.dumps([report["report_id"], report["sha256"], format, RENDERER_VERSION]).encode()
        ).hexdigest()

    def path(self, report, format, suffix):
        return self.root / (self.key(report, format) + "." + suffix)

    @staticmethod
    def atomic(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def write_if_current(self, report, format, suffix, content):
        """정본 존재·해시 판정부터 파일 교체까지 삭제 트랜잭션과 직렬화한다.

        await 없는 짧은 경계다. 삭제 전에 썼으면 purge가 지우고, 삭제 커밋 후면
        본문/실패/취소/재시작 메타데이터 어느 것도 다시 쓰지 않는다.
        메모리 무효화 집합이 아니므로 새 변환 소유자에서도 같은 판정이다.
        """
        with self.service.repo.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM reports WHERE report_id=?", (report["report_id"],)
            ).fetchone()
            if row is None or json.loads(row[0])["sha256"] != report["sha256"]:
                return False
            self.atomic(self.path(report, format, suffix), content)
            db.commit()
        return True

    def save(self, report, entry):
        try:
            self.write_if_current(
                report,
                entry["format"],
                "json",
                json.dumps(entry, ensure_ascii=False).encode(),
            )
        except OSError as error:
            raise ApiError(
                500, "export_failed", "변환 파일 상태를 저장할 수 없습니다. 다시 시도해 주세요."
            ) from error

    def entry(self, report, format):
        path = self.path(report, format, "json")
        empty = self.empty(report, format)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or set(value) != set(empty)
                or any(
                    value[k] != empty[k]
                    for k in ("report_id", "source_report_hash", "format", "renderer_version")
                )
                or value["status"] not in {"generating", "ready", "failed"}
                or any(
                    value[k] is not None and not isinstance(value[k], str)
                    for k in ("created_at", "updated_at")
                )
            ):
                raise ValueError("invalid export metadata")
            if value["status"] == "ready":
                if (
                    value["error"] is not None
                    or not isinstance(value["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
                    or value["download_url"] != self.download_url(report, format)
                ):
                    raise ValueError("invalid ready metadata")
                self.verified_bytes(report, value)
            else:
                if value["sha256"] is not None or value["download_url"] is not None:
                    raise ValueError("non-ready metadata has artifact")
                error = value["error"]
                if value["status"] == "generating" and error is not None:
                    raise ValueError("generating has error")
                if value["status"] == "failed" and (
                    not isinstance(error, dict)
                    or set(error) != {"code", "message"}
                    or error["code"] not in SAFE_ERRORS
                    or error["message"] != SAFE_ERRORS[error["code"]]
                ):
                    raise ValueError("invalid safe error")
            if value["status"] == "generating" and self.key(report, format) not in self.tasks:
                self.failed(value, "export_interrupted")
                self.save(report, value)
            return value
        except FileNotFoundError:
            if not path.exists():
                return empty
            raise
        except (OSError, ValueError, TypeError, KeyError, ApiError):
            self.failed(empty, "export_integrity_error")
            self.save(report, empty)
            return empty

    @staticmethod
    def failed(entry, code):
        entry.update(
            status="failed",
            updated_at=now(),
            sha256=None,
            download_url=None,
            error={"code": code, "message": SAFE_ERRORS[code]},
        )

    def verified_bytes(self, report, entry):
        try:
            content = self.path(report, entry["format"], entry["format"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError("hash mismatch")
            return content
        except (OSError, ValueError) as error:
            raise ApiError(
                409, "export_integrity_error", "변환 파일을 검증할 수 없습니다."
            ) from error

    def list(self, report_id):
        report, _, _ = self.service.report_bytes(report_id)
        return {
            "report_id": report_id,
            "source_report_hash": report["sha256"],
            "exports": [self.entry(report, format) for format in ("html", "pdf")],
        }

    @staticmethod
    def empty(report, format):
        return {
            "report_id": report["report_id"],
            "source_report_hash": report["sha256"],
            "format": format,
            "renderer_version": RENDERER_VERSION,
            "status": "not_requested",
            "created_at": None,
            "updated_at": None,
            "sha256": None,
            "download_url": None,
            "error": None,
        }

    def request(self, report_id, format, source_hash):
        report, content, _ = self.service.report_bytes(report_id)
        self.binding(report, source_hash)
        if len(content) > MAX_SOURCE_BYTES:
            raise ApiError(413, "export_too_large", "변환 원문은 1MiB 이하여야 합니다.")
        entry = self.entry(report, format)
        if entry["status"] in {"ready", "generating"}:
            return entry
        entry.update(
            status="generating",
            created_at=entry["created_at"] or now(),
            updated_at=now(),
            error=None,
            sha256=None,
            download_url=None,
        )
        self.save(report, entry)
        key = self.key(report, format)
        task = asyncio.create_task(self.generate(report, content, copy.deepcopy(entry)))
        self.tasks[key] = task

        def finished(done):
            if self.tasks.get(key) is done:
                self.tasks.pop(key, None)
            if not done.cancelled():
                done.exception()  # 저장장치 오류도 미수거 task 예외로 누출하지 않는다.

        task.add_done_callback(finished)
        return entry

    async def generate(self, report, content, entry):
        try:
            if entry["format"] == "pdf":
                async with self.pdf_slot:
                    rendered = await self.convert(content, entry["format"], report)
            else:
                rendered = await self.convert(content, entry["format"], report)
            if not self.write_if_current(report, entry["format"], entry["format"], rendered):
                return
            entry.update(
                status="ready",
                updated_at=now(),
                sha256=hashlib.sha256(rendered).hexdigest(),
                download_url=self.download_url(report, entry["format"]),
            )
        except (TimeoutError, PlaywrightTimeoutError):
            self.failed(entry, "export_timeout")
        except asyncio.CancelledError:
            self.failed(entry, "export_interrupted")
            self.save(report, entry)
            raise
        except Exception:  # noqa: BLE001 -- 앱 소유 작업 실패는 안전한 형식별 상태로 격리한다.
            self.failed(entry, "export_failed")
        self.save(report, entry)

    @staticmethod
    def download_url(report, format):
        query = urlencode(
            {"source_report_hash": report["sha256"], "renderer_version": RENDERER_VERSION}
        )
        return f"/api/spike/reports/{quote(report['report_id'], safe='')}/exports/{format}/download?{query}"

    async def convert(self, content, format, report):
        return await asyncio.wait_for(
            self.renderer(content, format, copy.deepcopy(report)), CONVERSION_TIMEOUT
        )

    def download(self, report_id, format, source_hash, renderer_version):
        report, _, _ = self.service.report_bytes(report_id)
        self.binding(report, source_hash, renderer_version)
        entry = self.entry(report, format)
        if entry["status"] == "failed" and entry["error"]["code"] == "export_integrity_error":
            raise ApiError(409, "export_integrity_error", entry["error"]["message"])
        if entry["status"] != "ready":
            raise ApiError(409, "export_not_ready", "아직 준비되지 않은 형식입니다.")
        content = self.verified_bytes(report, entry)
        return content, f"연구보고서_{report['report_id']}.{format}", entry

    @staticmethod
    def binding(report, source_hash, renderer_version=RENDERER_VERSION):
        if source_hash != report["sha256"] or renderer_version != RENDERER_VERSION:
            raise ApiError(409, "report_hash_mismatch", "현재 정본·렌더러와 요청이 다릅니다.")

    def files_of(self, report_ids):
        """보고서 ID 집합에 속한 변환 파일 경로 목록(읽기 전용).

        항목 `<key>.json`의 report_id로 소속을 판정하고, 같은 이름의 `.html`/`.pdf`
        본문을 함께 돌려준다. 폴더가 없거나 읽을 수 없는 항목은 건너뛴다.
        """
        ids = set(report_ids)
        if not ids or not self.root.is_dir():
            return []
        found = []
        for entry in sorted(self.root.glob("*.json")):
            try:
                value = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(value, dict) or value.get("report_id") not in ids:
                continue
            found.append(entry)
            found.extend(
                body
                for body in (entry.with_suffix(".html"), entry.with_suffix(".pdf"))
                if body.is_file()
            )
        return found

    def count_files(self, report_ids):
        return len(self.files_of(report_ids))

    def purge(self, report_ids):
        """보고서들의 변환 항목과 파일을 지운다. 실패는 예외가 아니라 errors로 모은다."""
        deleted, errors = 0, []
        for path in self.files_of(report_ids):
            try:
                path.unlink()
                deleted += 1
            except OSError:
                errors.append(f"변환 파일을 지우지 못했습니다: {path.name}")
        return {"deleted": deleted, "errors": errors}

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
