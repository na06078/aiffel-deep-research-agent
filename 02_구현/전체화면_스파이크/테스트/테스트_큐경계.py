"""큐 복구의 거절/실제 start 재사용 경계. 외부 통신 금지."""

import socket
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from 테스트 import 테스트_큐복구 as recovery_cases

setup = recovery_cases.setup
seed = recovery_cases.seed


async def test_retry_동시중복은단일spawn_재시작뒤응답도영속(setup):
    from concurrent.futures import ThreadPoolExecutor

    from 서버.실행제어 import ExecutionService

    service, _client, spawned = setup
    first, _ = seed(service)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: service.retry_run(first["run_id"], 1, "concurrent"), range(4))
        )
    assert all(response == responses[0] for response in responses)
    assert len(spawned) == 2 and len(service.repo.snapshot()["runs"]) == 2
    restored = ExecutionService(service.repo.root)
    restored_spawn = []
    restored.spawn = lambda *args: restored_spawn.append(args)
    try:
        assert restored.retry_run(first["run_id"], 1, "concurrent") == responses[0]
        assert not restored_spawn
        assert restored.repo.snapshot()["runs"][-1]["status"] == "interrupted"
    finally:
        await restored.close()


async def test_cancel_두항목의요청ID충돌은다른대기를제거하지않음(setup):
    service, client, _ = setup
    _, before = seed(service)
    first, second = [q["instruction_id"] for q in before["queue"]]
    assert (
        await client.post(f"/api/spike/queue/{first}/cancel", json={"client_request_id": "fixed"})
    ).status_code == 200
    stable = service.repo.path.read_bytes()
    response = await client.post(
        f"/api/spike/queue/{second}/cancel", json={"client_request_id": "fixed"}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "request_id_conflict"
    assert service.repo.path.read_bytes() == stable


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)


@pytest.mark.parametrize("status", ["blocked", "cancelled", "interrupted"])
async def test_retry_terminal_허용(setup, status):
    service, client, _ = setup
    first, _ = seed(service, status)
    response = await client.post(
        f"/api/spike/runs/{first['run_id']}/retry",
        json={"control_revision": 1, "client_request_id": "retry"},
    )
    assert response.status_code == 202


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", "not_found"),
        ("revision", "revision_conflict"),
        ("past", "revision_conflict"),
        ("running", "retry_unavailable"),
        ("completed", "retry_unavailable"),
        ("other_running", "retry_unavailable"),
        ("source", "source_mode_unavailable"),
        ("request", "request_id_conflict"),
    ],
)
async def test_retry_거절은무변경(setup, case, code):
    service, client, spawned = setup
    first, _ = seed(service)
    target, revision, request_id = first["run_id"], 1, "retry"
    if case == "missing":
        target = "absent"
    if case == "revision":
        revision = 2
    if case == "request":
        request_id = "first"
    with service.repo.transaction() as (_, data):
        run = data["runs"][0]
        if case in {"running", "completed"}:
            run["status"] = case
        if case == "past":
            data["current_run_id"] = "another"
        if case == "other_running":
            data["runs"].append({**deepcopy(run), "run_id": "other", "status": "running"})
        if case == "source":
            run["source_mode"] = "unavailable"
    before = service.repo.path.read_bytes()
    response = await client.post(
        f"/api/spike/runs/{target}/retry",
        json={"control_revision": revision, "client_request_id": request_id},
    )
    assert response.status_code == (404 if case == "missing" else 409)
    assert response.json()["error"]["code"] == code
    assert service.repo.path.read_bytes() == before
    assert len(spawned) == 1


@pytest.mark.parametrize("revision", [0, -1, True, "1", 1.5, None])
async def test_retry_필드오류422_무변경(setup, revision):
    service, client, spawned = setup
    first, _ = seed(service)
    before = service.repo.path.read_bytes()
    response = await client.post(
        f"/api/spike/runs/{first['run_id']}/retry",
        json={"control_revision": revision, "client_request_id": "invalid"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert service.repo.path.read_bytes() == before
    assert len(spawned) == 1


@pytest.mark.parametrize("case", ["absent", "started", "conflict"])
async def test_queue_cancel_없는대기와충돌_무변경(setup, case):
    service, client, spawned = setup
    first, before = seed(service)
    target = before["queue"][0]["instruction_id"]
    if case == "absent":
        target = "absent"
    if case == "started":
        target = first["instruction_id"]
    request_id = "q2" if case == "conflict" else "cancel"
    before_bytes = service.repo.path.read_bytes()
    response = await client.post(
        f"/api/spike/queue/{target}/cancel", json={"client_request_id": request_id}
    )
    assert response.status_code == (409 if case == "conflict" else 404)
    assert response.json()["error"]["code"] == (
        "request_id_conflict" if case == "conflict" else "not_found"
    )
    assert service.repo.path.read_bytes() == before_bytes
    assert len(spawned) == 1


async def test_real_start_override_재시도에서사용_자료유지(tmp_path):
    from 서버.실제연구제어 import RealResearchService

    class NoProvider:
        async def aclose(self):
            pass

    corpus = Path(__file__).resolve().parents[3] / "01_참고자료/원본데이터/corpus.json"
    service = RealResearchService(tmp_path, NoProvider(), corpus)
    spawned = []
    service.spawn = lambda *args: spawned.append(args)
    try:
        first, _ = seed(service)
        admission = service.retry_run(first["run_id"], 1, "real-retry")
        run = service.repo.snapshot()["runs"][-1]
        assert run["instruction_history"] == ["원 질문"]
        assert run["source_mode"] == admission["source_mode"] == "local"
        assert run["retry_of_run_id"] == first["run_id"]
        assert len(spawned) == 2
    finally:
        await service.close()
