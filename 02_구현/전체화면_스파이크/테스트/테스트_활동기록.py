"""실제 그래프·대역 gate의 경과 측정(중첩 구간, CPU 시간이 아님)."""

import asyncio
import math
from datetime import datetime

from 테스트.테스트_실행제어 import client_at, command, release, until


async def archive(client, run_id):
    response = await client.get(f"/api/spike/runs/{run_id}/archive")
    assert response.status_code == 200, response.text
    return response.json()


async def test_동시4개_gate대기와_실제코드단계를_구분해_영속측정한다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        run_id = (await command(client, "합성 시간 측정", "a")).json()["run_id"]
        active = await until(client, lambda s: len(s["waiting"]) == 4)
        live = await archive(client, run_id)
        waiting = [
            s for s in live["spans"] if s["kind"] == "model_wait" and s["status"] == "running"
        ]
        assert len(waiting) == 4
        assert {s["actor"] for s in waiting} == {f"researcher-{i}" for i in range(1, 5)}
        assert all(s["duration_ms"] is None and s["ended_at"] is None for s in waiting)
        await asyncio.sleep(0.08)
        await release(client, active["waiting"])
        await until(client, lambda s: s["runs"][0]["status"] == "completed")
        await asyncio.gather(*list(app.state.service.tasks))
        done = await archive(client, run_id)
        spans = done["spans"]
        assert done["has_measured_spans"] is True
        assert len({s["span_id"] for s in spans}) == len(spans)
        nodes = [s for s in spans if s["kind"] == "node"]
        assert {s["label"] for s in nodes} == {
            "plan",
            "dispatch",
            "research",
            "review",
            "synthesize",
            "evaluate",
            "publish",
        }
        assert {
            s["track_id"] for s in nodes if s["label"] in {"dispatch", "review", "evaluate"}
        } == {"code"}
        assert next(s for s in nodes if s["label"] == "publish")["track_id"] == "document"
        events = {e["event_id"]: e for e in done["events"]}
        with app.state.service.repo.connection() as db:
            calls = {r[0] for r in db.execute("SELECT call_id FROM calls")}
        for span in spans:
            assert span["run_id"] == run_id and span["control_revision"] == 1
            assert span["status"] == "completed"
            assert span["timing_source"] == "monotonic"
            assert math.isfinite(span["duration_ms"]) and span["duration_ms"] >= 0
            assert datetime.fromisoformat(span["ended_at"]) >= datetime.fromisoformat(
                span["started_at"]
            )
            assert span["event_ids"]
            assert all(
                events[e]["run_id"] == run_id and events[e]["control_revision"] == 1
                for e in span["event_ids"]
            )
            assert set(span) == {
                "span_id",
                "run_id",
                "control_revision",
                "actor",
                "track_id",
                "label",
                "kind",
                "started_at",
                "ended_at",
                "duration_ms",
                "status",
                "timing_source",
                "event_ids",
            }
        for wait in [s for s in spans if s["kind"] == "model_wait"]:
            starts = [events[e] for e in wait["event_ids"] if events[e]["type"] == "model.started"]
            assert len(starts) == 1 and starts[0]["output"]["call_id"] in calls
            assert len(wait["event_ids"]) >= 2
            if wait["actor"].startswith("researcher-"):
                assert wait["duration_ms"] >= 80
                node = next(s for s in nodes if s["actor"] == wait["actor"])
                assert node["duration_ms"] >= wait["duration_ms"]
        assert done["run"]["mock_requests"] == 6
        assert done["run"]["actual_model_requests"] == 0
        # 구간은 합산하지 않는다. 병렬 대기 4개의 합보다 실행 경과가 짧다.
        waits = [
            s for s in spans if s["kind"] == "model_wait" and s["actor"].startswith("researcher-")
        ]
        assert done["duration_ms"] < sum(s["duration_ms"] for s in waits)


async def test_재시작은_관측못한_열린구간의_시간을_발명하지않는다(tmp_path):
    import sqlite3

    source = tmp_path / "원본"
    crash = tmp_path / "중단스냅샷"
    crash.mkdir()
    async with client_at(source) as (client, app):
        run_id = (await command(client, "합성 비정상 중단", "a")).json()["run_id"]
        await until(client, lambda s: len(s["waiting"]) == 4)
        live = await archive(client, run_id)
        opened = {s["span_id"] for s in live["spans"] if s["status"] == "running"}
        assert len(opened) == 8
        # 프로세스를 죽이지 않고 SQLite backup으로 동일한 미종료 저널을 재현한다.
        with app.state.service.repo.connection() as db:
            dest = sqlite3.connect(crash / "실행기록.sqlite3")
            try:
                db.backup(dest)
            finally:
                dest.close()
    async with client_at(crash) as (client, app):
        restored = await archive(client, run_id)
        for span in restored["spans"]:
            if span["span_id"] in opened:
                assert span["status"] == "interrupted"
                assert span["duration_ms"] is None and span["ended_at"] is None
                assert span["timing_source"] == "incomplete"
        assert restored["run"]["status"] == "interrupted"
        assert restored["run"]["mock_requests"] == 5
        assert restored["run"]["actual_model_requests"] == 0
        assert not app.state.service.tasks and not app.state.service.gates
        first_duration = restored["duration_ms"]
        await asyncio.sleep(0.03)
        assert (await archive(client, run_id))["duration_ms"] == first_duration


async def test_새버전완료와_늦은4결과는_원래버전구간으로만_남는다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        run_id = (await command(client, "합성 옛 버전", "a")).json()["run_id"]
        old = await until(client, lambda s: len(s["waiting"]) == 4)
        await command(client, "합성 새 버전", "b")
        both = await until(client, lambda s: len(s["waiting"]) == 8)
        await release(client, [g for g in both["waiting"] if not g["is_stale"]])
        await until(client, lambda s: s["runs"][0]["status"] == "completed")
        before = await archive(client, run_id)
        reports = {p.name: p.read_bytes() for p in tmp_path.glob("*.md")}
        await release(client, old["waiting"])
        await asyncio.gather(*list(app.state.service.tasks))
        after = await archive(client, run_id)
        assert after["run"] == before["run"]
        assert {p.name: p.read_bytes() for p in tmp_path.glob("*.md")} == reports
        assert {r["control_revision"] for r in after["revisions"]} == {1, 2}
        stale = [
            s
            for s in after["spans"]
            if s["control_revision"] == 1 and s["actor"].startswith("researcher-")
        ]
        assert len(stale) == 8 and all(s["status"] == "superseded" for s in stale)
        assert all(s["status"] == "completed" for s in after["spans"] if s["control_revision"] == 2)
        events = {e["event_id"]: e for e in after["events"]}
        assert all(
            events[e]["control_revision"] == s["control_revision"]
            for s in after["spans"]
            for e in s["event_ids"]
        )
        assert after["run"]["mock_requests"] == 11 and after["run"]["actual_model_requests"] == 0


async def test_제출직후_다음큐가_시작돼도_평가와출판구간은_완료다(tmp_path):
    async with client_at(tmp_path) as (client, _app):
        run_id = (await command(client, "합성 먼저", "a")).json()["run_id"]
        active = await until(client, lambda s: len(s["waiting"]) == 4)
        await command(client, "/큐 합성 다음", "b")
        await release(client, active["waiting"])
        await until(client, lambda s: len(s["runs"]) == 2 and len(s["waiting"]) == 4)
        record = await archive(client, run_id)
        assert record["run"]["status"] == "completed"
        final_nodes = [s for s in record["spans"] if s["label"] in {"evaluate", "publish"}]
        assert len(final_nodes) == 2 and all(s["status"] == "completed" for s in final_nodes)
        assert all(s["status"] != "running" for s in record["spans"])


async def test_협력종료는_모든대기구간을_닫고_재시작때_재호출하지않는다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        run_id = (await command(client, "합성 협력종료", "a")).json()["run_id"]
        await until(client, lambda s: len(s["waiting"]) == 4)
    closed = app.state.service.repo.archive(run_id)
    cancelled = [s for s in closed["spans"] if s["actor"].startswith("researcher-")]
    assert len(cancelled) == 8
    assert all(
        s["status"] == "cancelled" and s["ended_at"] and s["duration_ms"] >= 0 for s in cancelled
    )
    assert not [s for s in closed["spans"] if s["status"] == "running"]
    async with client_at(tmp_path) as (client, app):
        restored = await archive(client, run_id)
        assert restored["spans"] == closed["spans"]
        assert restored["run"]["mock_requests"] == 5 and not app.state.service.tasks


async def test_오류와_품질차단은_실제결과구간을_보존하고_추가호출하지않는다(tmp_path):
    for outcome, expected in [("failure", "failed"), ("quality_fail", "blocked")]:
        async with client_at(tmp_path / outcome) as (client, app):
            run_id = (await command(client, "합성 오류 시간", "a")).json()["run_id"]
            active = await until(client, lambda s: len(s["waiting"]) == 4)
            await release(client, active["waiting"], outcome)
            await asyncio.gather(*list(app.state.service.tasks))
            record = await archive(client, run_id)
            assert record["run"]["status"] == expected
            assert all(s["status"] != "running" for s in record["spans"])
            assert not list((tmp_path / outcome).glob("*.md"))
            assert not [s for s in record["spans"] if s["label"] == "publish"]
            if outcome == "failure":
                assert any(s["status"] == "failed" for s in record["spans"])
                assert record["run"]["mock_requests"] == 5
            else:
                assert all(s["status"] == "completed" for s in record["spans"])
                assert record["run"]["mock_requests"] == 6
            assert record["run"]["actual_model_requests"] == 0
