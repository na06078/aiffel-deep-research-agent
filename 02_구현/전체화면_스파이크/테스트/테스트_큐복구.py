"""큐 복구: 실제 ASGI/SQLite, 감독 작업만 기록 대역; 외부 호출 없음."""

import asyncio
import time

import httpx
import pytest

from 서버.앱 import create_app


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    import socket

    import httpcore

    def denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    async def async_denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", async_denied)


async def test_retry_자동보완합격의정지해제도_기존큐정책을덮지못함(setup):
    service, _client, spawned = setup
    first, before = seed(service)
    service.retry_run(first["run_id"], 1, "retry-correction")
    # 실제 evaluate의 correction 합격 분기가 큐 정지를 해제한 직후를 재현한다.
    with service.repo.transaction() as (_, data):
        data["runs"][-1].update(status="completed", correction_state={"status": "completed"})
        data.update(queue_paused=False, queue_pause_reason=None)
    assert service.start_next_queue() is None
    after = service.repo.snapshot()
    assert after["queue"] == before["queue"]
    assert after["queue_paused"] is True and after["queue_pause_reason"] == "original"
    assert len(spawned) == 2
    service.continue_queue("explicit-resume")
    resumed = service.repo.snapshot()
    assert resumed["queue_paused"] is False and len(resumed["queue"]) == 1
    assert resumed["runs"][-1]["question"] == "대기1"


async def test_retry_실제감독그래프_합격뒤에도원정지유지(setup):
    service, client, _spawned = setup
    first, before = seed(service)
    del service.spawn
    response = await client.post(
        f"/api/spike/runs/{first['run_id']}/retry",
        json={"control_revision": 1, "client_request_id": "graph-retry"},
    )
    assert response.status_code == 202
    deadline = time.monotonic() + 5
    while len(service.gates) < 4 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert len(service.gates) == 4
    service.release(list(service.gates), "success")
    await asyncio.wait_for(asyncio.gather(*list(service.tasks)), timeout=5)
    after = service.repo.snapshot()
    run = next(r for r in after["runs"] if r["run_id"] == response.json()["run_id"])
    assert run["status"] == "completed" and run["report_id"]
    assert after["queue"] == before["queue"]
    assert after["queue_paused"] is True and after["queue_pause_reason"] == "original"
    assert after["runs"][0] == before["runs"][0]
    assert len(after["runs"]) == 2 and after["actual_model_requests"] == 0


async def test_cancel_대기하나만제거_중복_다른상태보존(setup):
    service, client, spawned = setup
    _first, before = seed(service)
    target = before["queue"][0]["instruction_id"]
    response = await client.post(
        f"/api/spike/queue/{target}/cancel", json={"client_request_id": "cancel-q"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"instruction_id": target, "status": "cancelled"}
    after = service.repo.snapshot()
    assert after["queue"] == before["queue"][1:]
    for field in (
        "runs",
        "current_run_id",
        "queue_paused",
        "queue_pause_reason",
        "actual_model_requests",
    ):
        assert after[field] == before[field]
    duplicate = await client.post(
        f"/api/spike/queue/{target}/cancel", json={"client_request_id": "cancel-q"}
    )
    assert duplicate.json() == response.json()
    assert service.repo.snapshot() == after
    assert len(spawned) == 1
    assert sum(e["type"] == "instruction.cancelled" for e in after["events"]) == 1


@pytest.fixture
async def setup(tmp_path):
    app = create_app(tmp_path)
    async with app.router.lifespan_context(app):
        service = app.state.service
        spawned = []
        service.spawn = lambda *args: spawned.append(args)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            yield service, client, spawned


def seed(service, status="failed"):
    first = service.admit("원 질문", "first")
    service.admit("/큐 대기1", "q1")
    service.admit("/큐 대기2", "q2")
    with service.repo.transaction() as (db, data):
        run = service.repo.run(data, first["run_id"])
        run.update(
            status=status,
            phase=status,
            draft="원 초안",
            failure={"code": "original", "message": "실패", "actor": "writer"},
        )
        data.update(queue_paused=True, queue_pause_reason="original")
        service.repo.save_revision(db, run)
    return first, service.repo.snapshot()


async def test_retry_새실행_멱등_원기록_큐정책보존(setup):
    service, client, spawned = setup
    first, before = seed(service)
    with service.repo.read_connection() as db:
        old_revision = db.execute(
            "SELECT body FROM revisions WHERE run_id=?", (first["run_id"],)
        ).fetchone()[0]
    body = {"control_revision": 1, "client_request_id": "retry-1"}
    response = await client.post(f"/api/spike/runs/{first['run_id']}/retry", json=body)
    assert response.status_code == 202, response.text
    admission = response.json()
    assert admission["run_id"] != first["run_id"]
    assert admission["status"] == "received"
    after = service.repo.snapshot()
    assert after["runs"][0] == before["runs"][0]
    assert after["runs"][-1]["retry_of_run_id"] == first["run_id"]
    assert after["runs"][-1]["question"] == "원 질문"
    assert after["runs"][-1]["source_mode"] == "local"
    assert after["queue"] == before["queue"]
    assert (after["queue_paused"], after["queue_pause_reason"]) == (True, "original")
    assert (
        await client.post(f"/api/spike/runs/{first['run_id']}/retry", json=body)
    ).json() == admission
    assert len(spawned) == 2
    with service.repo.read_connection() as db:
        assert (
            db.execute("SELECT body FROM revisions WHERE run_id=?", (first["run_id"],)).fetchone()[
                0
            ]
            == old_revision
        )
    # 합격 뒤의 다음 큐 검사 역시 원래 정지를 해제하지 않는다.
    with service.repo.transaction() as (db, data):
        service.repo.run(data, admission["run_id"]).update(status="completed")
        assert service.take_next(db, data) is None
