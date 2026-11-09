"""승인된 로컬 스파이크의 ASGI 진입점."""

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException

from 서버.보고서변환 import ReportExports
from 서버.실행삭제 import RunDeletion
from 서버.실행제어 import ExecutionService
from 서버.저장소 import ApiError, encode
from 서버.접근경계 import LoopbackOnly


class Command(BaseModel):
    text: str
    source_mode: Literal["local", "web"] | None = None

    @field_validator("source_mode")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("source_mode must not be null")
        return value

    client_request_id: str = Field(min_length=1, max_length=200)


class Release(BaseModel):
    gate_ids: list[str]
    outcome: Literal["success", "failure", "quality_fail"]


class Continue(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=200)


class Cancel(Continue):
    run_id: str = Field(min_length=1)
    control_revision: int = Field(gt=0)


class DeleteRun(BaseModel):
    client_request_id: str = Field(min_length=1, max_length=200)
    confirm_run_id: str


class ExportRequest(BaseModel):
    format: Literal["html", "pdf"]
    source_report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def create_app(
    data_dir: Path | None = None,
    static_dir: Path | None = None,
    *,
    service_factory=None,
    export_renderer=None,
):
    root = Path(__file__).resolve().parents[3]
    configured = os.environ.get("SPIKE_DATA_DIR") if data_dir is None else None
    if configured:
        data_dir = Path(configured).resolve()
        if not data_dir.is_relative_to(root):
            raise ValueError("SPIKE_DATA_DIR는 프로젝트 내부 경로여야 합니다.")
    data_dir = data_dir or root / "03_실행데이터" / "전체화면_스파이크"
    static_dir = static_dir or Path(__file__).resolve().parents[1] / "화면" / "dist"

    @asynccontextmanager
    async def lifespan(app):
        factory = app.state.service_factory or ExecutionService
        app.state.service = factory(data_dir)
        app.state.exports = ReportExports(app.state.service, export_renderer)
        app.state.deletion = RunDeletion(app.state.service, app.state.exports)
        try:
            yield
        finally:
            try:
                await app.state.exports.close()
            finally:
                await app.state.service.close()

    app = FastAPI(lifespan=lifespan)
    app.state.service_factory = service_factory
    app.add_middleware(LoopbackOnly)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError):
        return JSONResponse(
            status_code=error.status,
            content={"error": {"code": error.code, "message": error.message}},
        )

    @app.get("/api/spike/snapshot")
    async def snapshot():
        service = app.state.service
        result = service.repo.snapshot()
        if service.repo.provider == "openai":
            budget = service.model.usage()
            result["budget"] = budget
            result["actual_model_requests"] = budget["attempt_count"]
        return result

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "invalid_request", "message": "요청 필드·타입을 확인해 주세요."}
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": "not_found" if error.status_code == 404 else "http_error",
                    "message": "요청한 경로를 찾을 수 없습니다.",
                }
            },
        )

    @app.get("/api/health")
    async def health():
        service = app.state.service
        repo = getattr(service, "repo", None)
        provider = getattr(repo, "provider", "test-double")
        actual = service.model.usage()["attempt_count"] if provider == "openai" else 0
        return {
            "status": "ok",
            "mode": getattr(repo, "mode", "spike"),
            "provider": provider,
            "actual_model_requests": actual,
        }

    @app.post("/api/spike/commands", status_code=202)
    async def commands(body: Command):
        return app.state.service.admit(
            body.text, body.client_request_id, source_mode=body.source_mode
        )

    @app.post("/api/spike/releases")
    async def releases(body: Release):
        if app.state.service.repo.provider != "test-double":
            raise ApiError(
                409, "test_controls_unavailable", "실제 연구에서는 모의 응답을 조작할 수 없습니다."
            )
        return app.state.service.release(body.gate_ids, body.outcome)

    @app.post("/api/spike/queue/continue")
    async def continue_queue(body: Continue):
        return app.state.service.continue_queue(body.client_request_id)

    @app.post("/api/spike/cancel")
    async def cancel(body: Cancel):
        return app.state.service.cancel(body.run_id, body.control_revision, body.client_request_id)

    @app.get("/api/spike/events")
    async def events(request: Request, after: int = 0):
        try:
            cursor = max(0, after, int(request.headers.get("last-event-id", "0")))
        except ValueError as error:
            raise ApiError(422, "invalid_cursor", "사건 커서는 정수여야 합니다.") from error

        async def stream():
            nonlocal cursor
            heartbeat = time.monotonic()
            while True:
                batch = app.state.service.repo.events_after(cursor)
                for event in batch:
                    yield f"id: {event['seq']}\ndata: {encode(event)}\n\n"
                    cursor = event["seq"]
                if time.monotonic() - heartbeat >= 15:
                    yield ": keepalive\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/spike/runs/{run_id}/archive")
    async def run_archive(run_id: str):
        return app.state.service.repo.archive(run_id)

    @app.get("/api/spike/runs/{run_id}/delete-preview")
    async def run_delete_preview(run_id: str):
        return app.state.deletion.preview(run_id)

    @app.post("/api/spike/runs/{run_id}/delete")
    async def run_delete(run_id: str, body: DeleteRun):
        return app.state.deletion.delete(run_id, body.confirm_run_id, body.client_request_id)

    @app.get("/api/spike/events/{event_id}")
    async def event_detail(event_id: str):
        return app.state.service.repo.get_event(event_id)

    @app.get("/api/spike/reports/{report_id}")
    async def report(report_id: str):
        return app.state.service.report_bytes(report_id)[0]

    @app.get("/api/spike/reports/{report_id}/presentation")
    async def report_presentation(report_id: str, source_report_hash: str, renderer_version: str):
        from 서버.보고서표현 import render_presentation

        report, markdown, _ = app.state.service.report_bytes(report_id)
        app.state.exports.binding(report, source_report_hash, renderer_version)
        # 조회는 캐시 생성이나 연구 접수가 아니다. 정본을 검증한 뒤 메모리에서만 표현한다.
        return JSONResponse(
            content=render_presentation(markdown, report),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/spike/reports/{report_id}/exports")
    async def report_exports(report_id: str):
        return app.state.exports.list(report_id)

    @app.post("/api/spike/reports/{report_id}/exports", status_code=202)
    async def create_export(report_id: str, body: ExportRequest):
        return app.state.exports.request(report_id, body.format, body.source_report_hash)

    @app.get("/api/spike/reports/{report_id}/exports/{format}/download")
    async def download_export(
        report_id: str,
        format: Literal["html", "pdf"],
        source_report_hash: str,
        renderer_version: str,
    ):
        content, filename, entry = app.state.exports.download(
            report_id, format, source_report_hash, renderer_version
        )
        return Response(
            content,
            media_type="text/html; charset=utf-8" if format == "html" else "application/pdf",
            headers={
                "Content-Disposition": "attachment; filename*=utf-8''" + quote(filename),
                "X-Content-SHA256": entry["sha256"],
                "X-Source-Report-SHA256": entry["source_report_hash"],
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/spike/reports/{report_id}/download")
    async def download(report_id: str):
        report, _content, filename = app.state.service.report_bytes(report_id)
        return FileResponse(
            app.state.service.repo.root / filename,
            filename=filename,
            media_type="text/markdown; charset=utf-8",
            headers={
                "X-Content-SHA256": report["sha256"],
            },
        )

    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="화면")
    else:

        @app.get("/")
        async def not_built():
            return {"mode": "spike", "message": "화면 빌드 전입니다. API는 사용할 수 있습니다."}

    return app
