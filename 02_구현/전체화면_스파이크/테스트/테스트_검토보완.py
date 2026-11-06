"""RR-01/02: 주입 모델과 격리 저널만 사용하며 실제 요청은 하지 않는다."""

import json

import pytest

from 서버.교안연구 import ResearchContractError
from 서버.실제연구제어 import RealResearchService
from 테스트.테스트_실제연구제어 import CORPUS


@pytest.mark.parametrize("ambiguous", [False, True], ids=["복구가능", "모호한시작"])
async def test_과거실제구간은_정확연결만_읽기보정하고_DB전체불변(tmp_path, ambiguous):
    from 서버.저장소 import encode

    service = RealResearchService(tmp_path, SequenceModel(False), CORPUS)
    try:
        state = start_research(service)
        with pytest.raises(ResearchContractError):
            await service.research(state)
        # 기존 사용자 자료가 아닌 이 테스트의 저널에만 과거 오염을 재현한다.
        with service.repo.transaction() as (db, _data):
            rows = db.execute("SELECT * FROM activity_spans ORDER BY rowid").fetchall()
            events = [json.loads(r[0]) for r in db.execute("SELECT body FROM events ORDER BY seq")]
            calls = [dict(r) for r in db.execute("SELECT * FROM calls ORDER BY rowid")]
            own_expected = [
                [
                    e["event_id"]
                    for e in events
                    if isinstance(e["output"], dict) and e["output"].get("call_id") == c["call_id"]
                ]
                for c in calls
            ]
            waits = []
            for row in rows:
                span = json.loads(row["body"])
                if span["kind"] != "model_wait":
                    continue
                span.pop("call_id", None)
                index = len(waits)
                span["event_ids"] = [
                    e["event_id"]
                    for e in events
                    if e["seq"]
                    >= next(x["seq"] for x in events if x["event_id"] == own_expected[index][0])
                ]
                span["status"] = "failed"
                if ambiguous and index == 0:
                    # 서로 다른 두 시작 사건이 같은 관측 구간에 포함되는 손상 사례.
                    span["ended_at"] = json.loads(rows[-1]["body"])["ended_at"]
                waits.append(span)
                db.execute(
                    "UPDATE activity_spans SET body=? WHERE span_id=?",
                    (encode(span), span["span_id"]),
                )
        before = service.repo.path.read_bytes()
        archive = service.repo.archive(state["run_id"])
        again = service.repo.archive(state["run_id"])
        assert service.repo.path.read_bytes() == before
        fixed = [s for s in archive["spans"] if s["kind"] == "model_wait"]
        assert fixed == [s for s in again["spans"] if s["kind"] == "model_wait"]
        for index, span in enumerate(fixed):
            if ambiguous and index == 0:
                assert span["event_ids"] == [] and not span.get("call_id")
                assert span["event_link_status"] == "unavailable"
            else:
                assert span["call_id"] == calls[index]["call_id"]
                assert span["status"] == "completed"
                assert span["event_ids"] == own_expected[index]
                assert span["started_at"] == waits[index]["started_at"]
                assert span["ended_at"] == waits[index]["ended_at"]
                assert span["duration_ms"] == waits[index]["duration_ms"]
        assert archive["events"] == events
        with service.repo.read_connection() as db:
            assert [dict(r) for r in db.execute("SELECT * FROM calls ORDER BY rowid")] == calls
    finally:
        await service.close()


@pytest.mark.parametrize(
    "code,status",
    [("provider_http", 401), ("provider_http", 429), ("timeout", None), ("budget_exceeded", None)],
)
async def test_안전제공자오류는_실제앱에서_상태와원인보존_큐정지(tmp_path, code, status):
    import httpx

    from 서버.연구앱 import create_research_app
    from 테스트.테스트_실제연구제어 import settle

    class SafeError(Exception):
        safe_message = "안전한 시험 오류입니다."

        def __init__(self):
            super().__init__("SECRET-RAW-provider-body")
            self.code, self.status = code, status
            self.attempt_id = None if code == "budget_exceeded" else "격리시도"

    class ErrorModel:
        calls = 0

        async def complete(self, *args, on_attempt, **kwargs):
            self.calls += 1
            if code != "budget_exceeded":
                on_attempt("격리시도")
            raise SafeError()

        def usage(self):
            return {"attempt_count": self.calls if code != "budget_exceeded" else 0}

        async def aclose(self):
            pass

    model = ErrorModel()
    app = create_research_app(data_dir=tmp_path / "격리", model=model)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        # 이미 존재하는 보고서 파일은 실패로 바뀌지 않는다.
        report_path = tmp_path / "격리" / "기존보고서.md"
        report_path.write_text("격리 보존 내용", encoding="utf-8")
        before_report = report_path.read_bytes()
        response = await client.post(
            "/api/spike/commands", json={"text": "격리 질문", "client_request_id": "첫접수"}
        )
        assert response.status_code == 202
        await client.post(
            "/api/spike/commands", json={"text": "/큐 후속보존", "client_request_id": "큐접수"}
        )
        service = app.state.service
        await settle(service)
        snap = (await client.get("/api/spike/snapshot")).json()
        assert snap["runs"][0]["status"] == "failed"
        assert snap["queue_paused"] and len(snap["queue"]) == 1
        assert snap["queue_pause_reason"] == (f"provider_http_{status}" if status else code)
        assert len(snap["runs"]) == 1 and model.calls == 1
        run_id = response.json()["run_id"]
        archive = (await client.get(f"/api/spike/runs/{run_id}/archive")).json()
        errors = [e for e in archive["events"] if e["error"]]
        assert len(errors) == 1 and errors[0]["type"] == "model.failed"
        details = (await client.get(f"/api/spike/events/{errors[0]['event_id']}")).json()
        expected = {
            "code": code,
            "status": status,
            "attempt_id": None if code == "budget_exceeded" else "격리시도",
        }
        assert {k: details["error"][k] for k in expected} == expected
        with service.repo.read_connection() as db:
            call = dict(db.execute("SELECT * FROM calls").fetchone())
            stored = json.loads(call["output"])
            assert {k: stored[k] for k in expected} == expected
            assert call["status"] == "failed"
            assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0
        assert "SECRET-RAW" not in json.dumps([archive, call])
        assert report_path.read_bytes() == before_report
        assert list(report_path.parent.glob("*.md")) == [report_path]
        assert all(s["status"] == "failed" for s in archive["spans"])


class SequenceModel:
    def __init__(self, failure=False):
        self.calls = []
        self.failure = failure

    async def complete(self, system, user, *, context, on_attempt, **kwargs):
        self.calls.append(context)
        attempt = f"시험시도-{len(self.calls)}"
        on_attempt(attempt)
        if len(self.calls) == 2 and self.failure:
            raise RuntimeError("비저장 원시 오류")
        content = (
            "문서 메모"
            if context["operation"] == "read_one"
            else json.dumps({"본문": "시험 본문", "충분": "false", "부족": ""}, ensure_ascii=False)
        )
        return {"content": content, "attempt_id": attempt}

    async def aclose(self):
        pass


def start_research(service):
    with service.repo.transaction() as (db, data):
        run, _ = service.start(db, data, "격리 조사", "시험지시")
    return {
        "run_id": run["run_id"],
        "revision": 1,
        "assignment": {
            "assignee": "researcher-1",
            "seed": next(iter(service.corpus.docs)),
            "budget": 1,
            "instruction": "근거 조사",
            "title": "시험절",
            "role": "원인 담당",
        },
        "prior": {},
        "avoid": [],
    }


@pytest.mark.parametrize("failure", [True, False], ids=["후속요청실패", "충분타입실패"])
async def test_앞선성공호출은_후속실패와_사건에서_독립된다(tmp_path, failure):
    model = SequenceModel(failure)
    service = RealResearchService(tmp_path, model, CORPUS)
    try:
        state = start_research(service)
        with pytest.raises(ResearchContractError):
            await service.research(state)
        with service.repo.read_connection() as db:
            calls = [dict(r) for r in db.execute("SELECT * FROM calls ORDER BY rowid")]
            spans = [
                json.loads(r[0])
                for r in db.execute("SELECT body FROM activity_spans ORDER BY rowid")
            ]
            events = [json.loads(r[0]) for r in db.execute("SELECT body FROM events ORDER BY seq")]
        waits = [s for s in spans if s["kind"] == "model_wait"]
        assert [c["status"] for c in calls] == ["completed", "failed" if failure else "completed"]
        assert [s["status"] for s in waits] == ["completed", "failed" if failure else "completed"]
        assert next(s for s in spans if s["kind"] == "node")["status"] == "failed"
        for span, call in zip(waits, calls):
            assert span["call_id"] == call["call_id"]
            expected = [
                e["event_id"]
                for e in events
                if (e.get("output") or {}).get("call_id") == call["call_id"]
                or (
                    isinstance(e.get("input"), dict)
                    and e["input"].get("call_id") == call["call_id"]
                )
            ]
            assert span["event_ids"] == expected
            assert len(expected) == 2
        assert not set(waits[0]["event_ids"]) & set(waits[1]["event_ids"])
        node = next(s for s in spans if s["kind"] == "node")
        assert node["event_ids"] == [
            e["event_id"] for e in events if e["actor"] in {"tool", "researcher-1"}
        ]
        assert all(s["ended_at"] and s["duration_ms"] >= 0 for s in spans)
    finally:
        await service.close()
