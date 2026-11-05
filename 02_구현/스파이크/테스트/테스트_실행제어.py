"""실제 ASGI·SQLite·LangGraph 경계의 합성 대역 검사."""

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
import pytest

from 서버.앱 import create_app

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


@asynccontextmanager
async def client_at(path):
    app = create_app(data_dir=path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        yield client, app


async def snapshot(client):
    response = await client.get("/api/spike/snapshot")
    assert response.status_code == 200, response.text
    return response.json()


async def until(client, predicate):
    for _ in range(300):
        state = await snapshot(client)
        if predicate(state):
            return state
        await asyncio.sleep(0.01)
    raise AssertionError(f"상태 도달 실패: {state}")


async def command(client, text, request_id):
    return await client.post(
        "/api/spike/commands", json={"text": text, "client_request_id": request_id}
    )


async def test_새계획은_과거응답을_기다리지_않는다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        first = await command(client, "합성 지시 A", "a")
        assert first.status_code == 202, first.text
        state_a = await until(client, lambda s: len(s["waiting"]) == 4)
        old = [g["gate_id"] for g in state_a["waiting"]]
        second = await command(client, "완전히 새 합성 지시 B", "b")
        assert second.status_code == 202
        state_b = await until(client, lambda s: len(s["waiting"]) == 8)
        run = state_b["runs"][0]
        assert run["run_id"] == first.json()["run_id"] == second.json()["run_id"]
        assert run["control_revision"] == 2
        assert all("완전히 새 합성 지시 B" in a["title"] for a in run["plan"])
        assert sum(g["is_stale"] for g in state_b["waiting"]) == 4
        assert any(
            e["type"] == "instruction.applied" and e["control_revision"] == 2
            for e in state_b["events"]
        )
        assert old == [g["gate_id"] for g in state_b["waiting"] if g["is_stale"]]
        assert state_b["mock_requests"] == 10
        assert state_b["actual_model_requests"] == 0
        assert set(app.state.service.graph.get_graph().nodes) >= {
            "plan",
            "dispatch",
            "researcher",
            "review",
            "synthesize",
            "evaluate",
        }


async def release(client, gates, outcome="success"):
    response = await client.post(
        "/api/spike/releases",
        json={
            "gate_ids": [g["gate_id"] if isinstance(g, dict) else g for g in gates],
            "outcome": outcome,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_최신_보고서_정본과_늦은_과거결과_차단(tmp_path):
    import hashlib

    async with client_at(tmp_path) as (client, _app):
        await command(client, "합성 옛 지시", "a")
        a = await until(client, lambda s: len(s["waiting"]) == 4)
        await command(client, "합성 새 지시", "b")
        b = await until(client, lambda s: len(s["waiting"]) == 8)
        current = [g for g in b["waiting"] if not g["is_stale"]]
        await release(client, current)
        done = await until(client, lambda s: s["runs"][0]["status"] == "completed")
        run = done["runs"][0]
        response = await client.get(f"/api/spike/reports/{run['report_id']}")
        assert response.status_code == 200
        report = response.json()
        assert report["control_revision"] == 2
        assert "합성" in report["title"] and "실제 LLM" in report["markdown"]
        assert "합성 새 지시" in report["markdown"] and len(report["markdown"]) >= 2000
        assert "«합성문서-1»" in report["markdown"]
        download = await client.get(report["download_url"])
        assert download.content == report["markdown"].encode("utf-8")
        assert hashlib.sha256(download.content).hexdigest() == report["sha256"]
        assert "filename*=utf-8''" in download.headers["content-disposition"].lower()
        from urllib.parse import unquote

        filename = unquote(download.headers["content-disposition"].split("''", 1)[1])
        assert filename == f"🧪 스파이크 보고서_{report['report_id']}.md"
        assert (tmp_path / filename).read_bytes() == download.content
        stable_run = run.copy()
        await release(client, a["waiting"])
        final = await until(
            client, lambda s: len([e for e in s["events"] if e["type"] == "result.superseded"]) == 4
        )
        assert not final["waiting"]
        assert final["runs"][0] == stable_run
        assert final["mock_requests"] == 11
        superseded = [e for e in final["events"] if e["type"] == "result.superseded"]
        assert len(superseded) == 4
        assert not [
            e
            for e in final["events"]
            if e["control_revision"] == 1 and e["actor"] in {"editor", "evaluator", "publisher"}
        ]
        assert (await release(client, a["waiting"])) == {"released_gate_ids": []}


async def test_큐는_넷중하나가_아닌_제출후_FIFO_시작(tmp_path):
    async with client_at(tmp_path) as (client, _app):
        first = await command(client, "/큐 합성 첫 지시", "a")
        assert first.status_code == 202 and first.json()["mode"] == "immediate"
        a = await until(client, lambda s: len(s["waiting"]) == 4)
        b = await command(client, "/큐 합성 둘째", "b")
        c = await command(client, "/큐 합성 셋째", "c")
        assert b.json()["status"] == c.json()["status"] == "queued"
        queued = await snapshot(client)
        assert [q["text"] for q in queued["queue"]] == ["합성 둘째", "합성 셋째"]
        assert queued["runs"][0]["control_revision"] == 1
        await release(client, a["waiting"][:1])
        await asyncio.sleep(0.05)
        one = await snapshot(client)
        assert len(one["runs"]) == 1 and len(one["queue"]) == 2
        assert one["runs"][0]["report_id"] is None
        await release(client, a["waiting"][1:])
        two = await until(client, lambda s: len(s["runs"]) == 2 and len(s["waiting"]) == 4)
        assert two["runs"][0]["status"] == "completed"
        assert two["runs"][1]["question"] == "합성 둘째"
        assert two["runs"][1]["parent_report_id"] == two["runs"][0]["report_id"]
        await release(client, two["waiting"])
        three = await until(client, lambda s: len(s["runs"]) == 3 and len(s["waiting"]) == 4)
        assert three["runs"][2]["question"] == "합성 셋째"
        for previous, following in zip(three["runs"], three["runs"][1:]):
            submitted = next(
                e["seq"]
                for e in three["events"]
                if e["run_id"] == previous["run_id"] and e["type"] == "report.submitted"
            )
            started = next(
                e["seq"]
                for e in three["events"]
                if e["run_id"] == following["run_id"] and e["type"] == "instruction.received"
            )
            assert submitted < started


@pytest.mark.parametrize("outcome,status", [("failure", "failed"), ("quality_fail", "blocked")])
async def test_실패는_큐보존_정지하고_일반입력도_정지를_풀지않는다(tmp_path, outcome, status):
    async with client_at(tmp_path) as (client, _app):
        await command(client, "합성 실패 시험", "a")
        a = await until(client, lambda s: len(s["waiting"]) == 4)
        await command(client, "/큐 보존할 합성 지시", "b")
        await release(client, a["waiting"], outcome)
        stopped = await until(client, lambda s: s["runs"][0]["status"] == status)
        assert stopped["queue_paused"] and len(stopped["queue"]) == 1
        assert stopped["runs"][0]["report_id"] is None
        assert not list(tmp_path.glob("*.md"))
        assert not [e for e in stopped["events"] if e["type"] == "report.submitted"]
        if outcome == "quality_fail":
            assert stopped["runs"][0]["draft"] and stopped["runs"][0]["quality"]["reasons"]
            assert stopped["runs"][0]["quality"]["passed"] is False
        else:
            errors = [e for e in stopped["events"] if e["type"] == "model.failed"]
            assert errors
        queued = await command(client, "/큐 정지중 추가", "q2")
        assert queued.json()["status"] == "queued"
        immediate = await command(client, "정지중 일반 합성 지시", "c")
        assert immediate.json()["mode"] == "immediate"
        active = await until(client, lambda s: len(s["waiting"]) == 4)
        await release(client, active["waiting"])
        finished = await until(client, lambda s: s["runs"][-1]["status"] == "completed")
        assert finished["queue_paused"] and len(finished["queue"]) == 2
        assert len(finished["runs"]) == 2
        resumed = await client.post(
            "/api/spike/queue/continue", json={"client_request_id": "resume"}
        )
        assert resumed.status_code == 200 and resumed.json()["status"] == "running"
        again = await until(client, lambda s: len(s["runs"]) == 3 and len(s["waiting"]) == 4)
        assert again["runs"][-1]["question"] == "보존할 합성 지시"
        assert not again["queue_paused"]


async def test_멱등_접수와_충돌_빈지시_미지gate는_부작용없다(tmp_path):
    async with client_at(tmp_path) as (client, _app):
        first = await command(client, "합성 양성 대조", "same")
        initial = await until(client, lambda s: len(s["waiting"]) == 4)
        duplicate = await command(client, "합성 양성 대조", "same")
        assert duplicate.json() == first.json()
        for _ in range(3):
            assert await snapshot(client) == initial
        conflict = await command(client, "다른 내용", "same")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "request_id_conflict"
        for text in ["", "   ", "/큐", "/큐    "]:
            bad = await command(client, text, "empty")
            assert bad.status_code == 422 and bad.json()["error"]["code"] == "empty_instruction"
        unknown = await client.post(
            "/api/spike/releases",
            json={"gate_ids": [initial["waiting"][0]["gate_id"], "unknown"], "outcome": "success"},
        )
        assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "gate_not_found"
        assert await snapshot(client) == initial


async def test_취소는_정확버전과_멱등성을_요구하고_늦은결과를_막는다(tmp_path):
    async with client_at(tmp_path) as (client, _app):
        await command(client, "합성 취소 시험", "a")
        initial = await until(client, lambda s: len(s["waiting"]) == 4)
        await command(client, "/큐 보존 지시", "b")
        before = await snapshot(client)
        payload = {
            "run_id": before["current_run_id"],
            "control_revision": 9,
            "client_request_id": "cancel",
        }
        wrong = await client.post("/api/spike/cancel", json=payload)
        assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "revision_conflict"
        assert await snapshot(client) == before
        payload["control_revision"] = 1
        ok = await client.post("/api/spike/cancel", json=payload)
        assert ok.status_code == 200 and ok.json() == {"status": "cancelled"}
        after = await snapshot(client)
        duplicate = await client.post("/api/spike/cancel", json=payload)
        assert duplicate.json() == ok.json() and await snapshot(client) == after
        payload["control_revision"] = 2
        conflict = await client.post("/api/spike/cancel", json=payload)
        assert (
            conflict.status_code == 409
            and conflict.json()["error"]["code"] == "request_id_conflict"
        )
        await release(client, initial["waiting"])
        final = await until(
            client, lambda s: len([e for e in s["events"] if e["type"] == "result.superseded"]) == 4
        )
        assert final["runs"][0]["status"] == "cancelled" and final["queue_paused"]
        assert len(final["queue"]) == 1 and not list(tmp_path.glob("*.md"))
        resumed = await client.post("/api/spike/queue/continue", json={"client_request_id": "go"})
        running = await until(client, lambda s: len(s["waiting"]) == 4)
        duplicate = await client.post("/api/spike/queue/continue", json={"client_request_id": "go"})
        assert duplicate.json() == resumed.json()
        assert await snapshot(client) == running


async def test_재시작은_미완료를_interrupted_보존하고_모델을_재호출하지않는다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        original = await command(client, "합성 재시작 시험", "same")
        initial = await until(client, lambda s: len(s["waiting"]) == 4)
        await command(client, "/큐 재시작 뒤에도 보존", "q")
        service = app.state.service
        owned_tasks = list(service.tasks)
    assert all(t.done() for t in owned_tasks) and not service.gates
    async with client_at(tmp_path) as (client, app):
        restored = await snapshot(client)
        assert restored["runs"][0]["run_id"] == initial["current_run_id"]
        assert restored["runs"][0]["status"] == "interrupted"
        assert restored["runs"][0]["plan"] == initial["runs"][0]["plan"]
        assert restored["queue_paused"] and len(restored["queue"]) == 1
        assert restored["mock_requests"] == initial["mock_requests"] == 5
        assert not restored["waiting"] and not app.state.service.tasks
        duplicate = await command(client, "합성 재시작 시험", "same")
        assert duplicate.json() == original.json()
        await asyncio.sleep(0.05)
        assert await snapshot(client) == restored
        assert (await release(client, initial["waiting"])) == {"released_gate_ids": []}
        assert (await snapshot(client))["mock_requests"] == 5
