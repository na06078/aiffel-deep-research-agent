"""실제 HTTP 읽기 경계: 전체 상세·SQLite/보고서 무변경."""

import asyncio
import json

from 테스트.테스트_실행제어 import client_at, command, release, until


def stored(repo):
    with repo.connection() as db:
        tables = [
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        return {
            t: [tuple(r) for r in db.execute(f'SELECT * FROM "{t}" ORDER BY rowid')] for t in tables
        }


async def test_전체상세는_100과200을_넘어도_잘리지않고_GET은_무변경(tmp_path):
    async with client_at(tmp_path) as (client, app):
        admitted = (await command(client, "합성 아카이브", "a")).json()
        active = await until(client, lambda s: len(s["waiting"]) == 4)
        # 기존 공개 큐 접수 경로로 200개를 넘긴다. 생성된 큐를 실행하지 않는다.
        for i in range(205):
            assert (await command(client, f"/큐 보존 {i}", f"q{i}")).status_code == 202
        repo = app.state.service.repo
        before = stored(repo)
        pages = repo.path.read_bytes()
        gates = dict(app.state.service.gates)
        tasks = set(app.state.service.tasks)
        url = f"/api/spike/runs/{admitted['run_id']}/archive"
        response = await client.get(url)
        assert response.status_code == 200, response.text
        archive = response.json()
        expected = [json.loads(r[2]) for r in before["events"]]
        assert archive["events"] == expected and len(expected) > 200
        assert archive["high_watermark_seq"] == expected[-1]["seq"]
        assert archive["run"]["run_id"] == admitted["run_id"]
        assert archive["started_at"] == archive["run"]["created_at"]
        assert archive["ended_at"] is None and archive["duration_ms"] >= 0
        assert archive["protocol_version"] == 1 and archive["provider"] == "test-double"
        assert archive["truncated"] is False
        assert set(archive) == {
            "protocol_version",
            "provider",
            "run",
            "revisions",
            "events",
            "spans",
            "high_watermark_seq",
            "started_at",
            "ended_at",
            "recorded_at",
            "duration_ms",
            "has_measured_spans",
            "truncated",
        }
        assert archive["revisions"] == [json.loads(r[2]) for r in before["revisions"]]
        missing = await client.get("/api/spike/runs/missing/archive")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
        assert stored(repo) == before and repo.path.read_bytes() == pages
        assert app.state.service.gates == gates and app.state.service.tasks == tasks
        assert active["actual_model_requests"] == archive["run"]["actual_model_requests"] == 0


async def test_안정된_반복스냅샷과_아카이브_GET은_쓰기SQL도_실행하지않는다(tmp_path, monkeypatch):
    import sqlite3

    async with client_at(tmp_path) as (client, app):
        admitted = (await command(client, "합성 읽기 전용 스냅샷", "a")).json()
        active = await until(client, lambda s: len(s["waiting"]) == 4)
        await release(client, active["waiting"])
        await until(client, lambda s: s["runs"][0]["status"] == "completed")
        await asyncio.gather(*list(app.state.service.tasks))
        repo = app.state.service.repo
        before = stored(repo)
        pages = repo.path.read_bytes()
        traced = []
        original = sqlite3.connect

        def traced_connection(*args, **kwargs):
            db = original(*args, **kwargs)
            db.set_trace_callback(traced.append)
            return db

        monkeypatch.setattr(sqlite3, "connect", traced_connection)
        for _ in range(3):
            snap = await client.get("/api/spike/snapshot")
            record = await client.get(f"/api/spike/runs/{admitted['run_id']}/archive")
            assert snap.status_code == record.status_code == 200
            assert snap.json()["runs"][0] == record.json()["run"]
            assert snap.json()["mock_requests"] == 6
        assert not [
            sql
            for sql in traced
            if sql.lstrip()
            .upper()
            .startswith(("UPDATE", "INSERT", "DELETE", "REPLACE", "BEGIN IMMEDIATE"))
        ], traced
        assert stored(repo) == before and repo.path.read_bytes() == pages


async def test_완료보고서와_과거실행시간은_조회때_늘거나_쓰이지않는다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        admitted = (await command(client, "합성 완료 기록", "a")).json()
        active = await until(client, lambda s: len(s["waiting"]) == 4)
        await release(client, active["waiting"])
        await until(client, lambda s: s["runs"][0]["status"] == "completed")
        await asyncio.gather(*list(app.state.service.tasks))
        repo = app.state.service.repo
        before = stored(repo)
        files = {p.name: p.read_bytes() for p in tmp_path.glob("*") if p.is_file()}
        url = f"/api/spike/runs/{admitted['run_id']}/archive"
        first = (await client.get(url)).json()
        await asyncio.sleep(0.03)
        second = (await client.get(url)).json()
        assert second["duration_ms"] == first["duration_ms"]
        assert second["ended_at"] == second["run"]["updated_at"]
        assert stored(repo) == before
        assert {p.name: p.read_bytes() for p in tmp_path.glob("*") if p.is_file()} == files
        report = (await client.get(f"/api/spike/reports/{first['run']['report_id']}")).json()
        assert (await client.get(report["download_url"])).content == report["markdown"].encode()


async def test_과거저널은_원래상세와_보고서를_보존하고_정밀시간을_합성하지않는다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        run_id = (await command(client, "합성 이전형식", "a")).json()["run_id"]
        active = await until(client, lambda s: len(s["waiting"]) == 4)
        await release(client, active["waiting"])
        await asyncio.gather(*list(app.state.service.tasks))
        url = f"/api/spike/runs/{run_id}/archive"
        full = (await client.get(url)).json()
        assert full["has_measured_spans"] is True
        # 이 테스트의 tmp DB만 이전 스키마로 만든다. 사용자 데이터에는 접근하지 않는다.
        with app.state.service.repo.connection() as db:
            db.execute("DROP TABLE activity_spans")
            db.commit()
        legacy = (await client.get(url)).json()
        assert legacy["spans"] == [] and legacy["has_measured_spans"] is False
        assert legacy["events"] == full["events"] and legacy["revisions"] == full["revisions"]
        assert legacy["run"] == full["run"]
        files = {p.name: p.read_bytes() for p in tmp_path.glob("*.md")}
    async with client_at(tmp_path) as (client, app):
        restored = (await client.get(url)).json()
        assert restored["spans"] == [] and restored["has_measured_spans"] is False
        assert restored["events"] == legacy["events"] and restored["run"] == legacy["run"]
        assert restored["run"]["mock_requests"] == 6 and not app.state.service.tasks
        assert {p.name: p.read_bytes() for p in tmp_path.glob("*.md")} == files
