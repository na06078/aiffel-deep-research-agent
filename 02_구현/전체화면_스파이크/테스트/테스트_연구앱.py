"""실제 앱 조립은 주입 대역으로, 실제 HTTP 제공자는 별도 승인 실행으로 검증한다."""

import asyncio
import json

import httpx

from 서버.연구앱 import create_research_app
from 테스트.테스트_실제연구제어 import ScriptedModel


async def test_실제앱_주입모델과_분리저장_정본다운로드(tmp_path):
    model = ScriptedModel()
    app = create_research_app(data_dir=tmp_path / "실제연구", model=model)
    async with app.router.lifespan_context(app):
        service = app.state.service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            health = (await client.get("/api/health")).json()
            assert health["mode"] == "research"
            assert health["provider"] == "openai"
            assert service.repo.root == (tmp_path / "실제연구").resolve()
            admitted = await client.post(
                "/api/spike/commands",
                json={
                    "text": "1894년에 일어난 주요 사건들은 무엇이고 각각의 원인은 무엇인가?",
                    "client_request_id": "real-http-test",
                },
            )
            assert admitted.status_code == 202
            for _ in range(1000):
                if not service.tasks:
                    break
                await asyncio.sleep(0.005)
            snapshot = (await client.get("/api/spike/snapshot")).json()
            run = snapshot["runs"][0]
            assert run["status"] == "completed"
            assert snapshot["actual_model_requests"] == len(model.calls)
            before = len(model.calls)
            report = (await client.get(f"/api/spike/reports/{run['report_id']}")).json()
            downloaded = await client.get(report["download_url"])
            assert downloaded.status_code == 200
            assert downloaded.content == report["markdown"].encode("utf-8")
            assert downloaded.headers["x-content-sha256"] == report["sha256"]
            archive = (await client.get(f"/api/spike/runs/{run['run_id']}/archive")).json()
            assert archive["provider"] == "openai"
            requests = [
                e
                for e in archive["events"]
                if e["type"] == "model.started"
                and e["input"]["context"]["operation"] == "draft_section"
            ]
            assert len(requests) == 5  # 기존 네 절 + 부족 절 한 번; 신규 모델 턴 없음.
            for event in requests:
                payload = event["input"]
                catalog = json.loads(payload["user"])["evidence"]
                assert catalog == payload["inputs"]["catalog"]
                token = json.loads(payload["user"])["evidence_set_id"]
                assert payload["response_schema"]["properties"]["근거목록ID"]["enum"] == [token]
                assert payload["response_schema"]["properties"]["문장"]["items"]["properties"][
                    "근거구간"
                ]["items"]["enum"] == [e["id"] for e in catalog]
                assert all(type(e["id"]) is int for e in catalog)
                assert set(payload["inputs"]["source_hashes"]) == {e["source_id"] for e in catalog}
            raw_drafts = [
                json.loads(e["output"]["content"])
                for e in archive["events"]
                if e["type"] == "model.completed" and '"문장"' in e["output"]["content"]
            ]
            assert len(raw_drafts) == 5
            assert all(set(raw) == {"근거목록ID", "문장", "충분", "부족"} for raw in raw_drafts)
            sections = [e["output"] for e in archive["events"] if e["type"] == "research.completed"]
            assert len(sections) == 5 and all(s["근거"] and "문장" not in s for s in sections)
            assert len(model.calls) == before
    assert model.closed
