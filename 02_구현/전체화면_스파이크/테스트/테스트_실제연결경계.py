"""실제 제공자 조립의 provenance·저장·HTTP 경계를 대역으로 검증한다."""

import httpx

from 서버 import 앱 as app_module
from 서버.실행제어 import ExecutionService
from 서버.저장소 import Repository


def test_저장소는_주입된_제공자와_실제시도를_조회에_보존한다(tmp_path):
    repo = Repository(tmp_path)
    # 먼저 기존 생성 경로를 통과한 뒤 공개 구성의 효과를 검사한다.
    repo.provider = "openai"
    repo.mode = "research"
    run = {
        "run_id": "real-boundary-run",
        "control_revision": 1,
        "status": "completed",
        "created_at": "2026-11-06T00:00:00+00:00",
        "updated_at": "2026-11-06T00:00:01+00:00",
        "mock_requests": 0,
        "actual_model_requests": 3,
    }
    with repo.transaction() as (db, state):
        state["runs"].append(run)
        state["current_run_id"] = run["run_id"]
        repo.save_revision(db, run)
        event = repo.event(db, run["run_id"], 1, "model.completed", "planner")
    snapshot = repo.snapshot()
    assert snapshot["provider"] == "openai"
    assert snapshot["mode"] == "research"
    assert snapshot["actual_model_requests"] == 3
    assert snapshot["mock_requests"] == 0
    assert event["provider"] == "openai"
    archive = repo.archive(run["run_id"])
    assert archive["provider"] == "openai"
    assert archive["events"][0]["provider"] == "openai"


def test_실제보고서는_합성제목과_파일명으로_게시되지_않는다(tmp_path):
    service = ExecutionService(tmp_path)
    service.report_title = "1894년 주요 사건과 원인"
    service.report_filename_prefix = "🧾 실제 연구 보고서"
    with service.repo.transaction() as (db, state):
        run, _ = service.start(db, state, "1894년 사건", "instruction-test")
        run["draft"] = "# 1894년 주요 사건과 원인\n\n직접 비교할 고정 본문."
        service.publish(db, state, run["run_id"], 1)
        report_id = run["report_id"]
    report, content, filename = service.report_bytes(report_id)
    assert report["title"] == "1894년 주요 사건과 원인"
    assert filename.startswith("🧾 실제 연구 보고서_")
    assert content.decode("utf-8") == report["markdown"]


async def test_실제조립의_health와_snapshot은_같은제공자이고_대역해제를_거절한다(tmp_path):
    class ModelMetadata:
        def usage(self):
            return {
                "attempt_count": 1,
                "max_attempts": 40,
                "remaining_attempts": 39,
                "spent_usd": 0.001,
                "reserved_usd": 0,
                "max_usd": 1,
                "input_tokens": 10,
                "output_tokens": 2,
                "unknown_count": 0,
            }

    class RealAssemblyStub:
        def __init__(self, data_dir):
            self.repo = Repository(data_dir)
            self.repo.provider = "openai"
            self.repo.mode = "research"
            self.model = ModelMetadata()

        async def close(self):
            return None

        def release(self, *args):
            raise AssertionError("실제 모드에서 대역 해제 함수에 진입하면 안 됩니다.")

    app = app_module.create_app(data_dir=tmp_path)
    app.state.service_factory = RealAssemblyStub
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        health = (await client.get("/api/health")).json()
        assert health["provider"] == "openai"
        assert health["mode"] == "research"
        assert health["actual_model_requests"] == 1
        snapshot = (await client.get("/api/spike/snapshot")).json()
        assert snapshot["provider"] == "openai"
        assert snapshot["budget"]["remaining_attempts"] == 39
        result = await client.post(
            "/api/spike/releases", json={"gate_ids": [], "outcome": "success"}
        )
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "test_controls_unavailable"
