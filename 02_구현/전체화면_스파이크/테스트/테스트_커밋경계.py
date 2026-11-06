"""커밋 경계 회귀: 이미 시작된 종합 응답도 새 revision을 덮지 못한다."""

import asyncio

from 서버.모델대역 import FakeModel
from 테스트.테스트_실행제어 import client_at, command, release, snapshot, until


async def test_늦은_종합원고와_평가_파일_커밋_경계를_직접_차단한다(tmp_path):
    entered = asyncio.Event()
    editor_gate = asyncio.get_running_loop().create_future()

    class DelayedSyntheticEditor(FakeModel):
        async def synthesize(self, payload):
            if payload["instruction"] == "합성 A":
                entered.set()
                await editor_gate
            return await super().synthesize(payload)

    async with client_at(tmp_path) as (client, app):
        service = app.state.service
        service.model = DelayedSyntheticEditor()
        await command(client, "합성 A", "a")
        a = await until(client, lambda s: len(s["waiting"]) == 4)
        await release(client, a["waiting"])
        await asyncio.wait_for(entered.wait(), 2)
        await command(client, "합성 B", "b")
        b = await until(client, lambda s: len(s["waiting"]) == 4)
        assert b["runs"][0]["control_revision"] == 2
        editor_gate.set_result(None)
        superseded = await until(
            client,
            lambda s: any(
                e["type"] == "result.superseded" and e["actor"] == "editor" for e in s["events"]
            ),
        )
        assert superseded["runs"][0]["draft"] is None
        assert not list(tmp_path.glob("*.md"))
        old = {
            "run_id": b["current_run_id"],
            "revision": 1,
            "instruction": "합성 A",
            "draft": "# 합성\n이 원고는 늦게 도착한 평가 입력이다.",
            "results": [],
        }
        # UI 필터가 아니라 실제 저장소/노드의 fence를 직접 공격한다.
        before = await snapshot(client)
        assert await service.synthesize(old) == {}
        assert await service.evaluate(old) == {}
        with service.repo.transaction() as (db, data):
            service.publish(db, data, old["run_id"], 1)
        assert await snapshot(client) == before
        assert not list(tmp_path.glob("*.md"))
        await release(client, b["waiting"])
        done = await until(client, lambda s: s["runs"][0]["status"] == "completed")
        assert done["runs"][0]["quality"]["passed"] is True
        assert not [
            e
            for e in done["events"]
            if e["control_revision"] == 1 and e["actor"] in {"evaluator", "publisher"}
        ]
        with service.repo.transaction() as (db, data):
            service.publish(db, data, old["run_id"], 2)
        assert await snapshot(client) == done
        assert len(list(tmp_path.glob("*.md"))) == 1


async def test_완료정본은_재시작뒤에도_같고_손상은_JSON과_다운로드_모두_차단한다(tmp_path):
    async with client_at(tmp_path) as (client, _app):
        await command(client, "합성 보존 시험", "a")
        running = await until(client, lambda s: len(s["waiting"]) == 4)
        await release(client, running["waiting"])
        done = await until(client, lambda s: s["runs"][0]["status"] == "completed")
        url = f"/api/spike/reports/{done['runs'][0]['report_id']}"
        original = (await client.get(url)).json()
    async with client_at(tmp_path) as (client, app):
        restored = await snapshot(client)
        assert restored["runs"] == done["runs"]
        assert restored["mock_requests"] == 6
        assert not app.state.service.tasks
        assert (await client.get(url)).json() == original
        assert (await client.get(original["download_url"])).content == original["markdown"].encode()
        file = next(tmp_path.glob("*.md"))
        file.write_bytes(b"damaged synthetic test artifact")
        for target in [url, original["download_url"]]:
            rejected = await client.get(target)
            assert rejected.status_code == 409
            assert rejected.json()["error"]["code"] == "report_integrity_error"
        assert await snapshot(client) == restored


async def test_호출시작전_취소된_graph는_새_대역요청조차_만들지않는다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        service = app.state.service
        admission = service.admit("합성 즉시 취소", "a")
        service.cancel(admission["run_id"], admission["control_revision"], "cancel")
        await asyncio.gather(*list(service.tasks))
        state = await snapshot(client)
        assert state["runs"][0]["status"] == "cancelled"
        assert state["mock_requests"] == 0
        assert not state["waiting"] and not list(tmp_path.glob("*.md"))
