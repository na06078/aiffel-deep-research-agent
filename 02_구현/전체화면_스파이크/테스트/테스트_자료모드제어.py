"""격리 저널의 자료 선택·멱등성·큐 경계. 외부 요청과 그래프 시작 없음."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from 서버.실행제어 import ExecutionService
from 서버.앱 import create_app
from 서버.저장소 import ApiError

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    import socket

    import httpcore
    import httpx

    def denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    async def async_denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", async_denied)


class QuietService(ExecutionService):
    @property
    def source_modes(self):
        return ["local", "web"]

    def __init__(self, root):
        super().__init__(root)
        self.spawned = []

    def spawn(self, *args):
        self.spawned.append(args)


@pytest.fixture
def service():
    return QuietService(ROOT / ".cache/웹검토_보완/단위" / uuid4().hex)


def saved(service):
    with service.repo.read_connection() as db:
        return {
            t: [tuple(r) for r in db.execute(f"SELECT * FROM {t}")]
            for t in ("state", "events", "requests", "revisions", "calls")
        }


def test_단독선택은_무실행_영속_일반질문은_선택사용(service):
    receipt = service.admit("/웹", "mode")
    assert receipt.get("kind") == "mode_change"
    assert receipt["source_mode"] == "web" and receipt["state_seq"] > 0
    assert not service.spawned
    assert service.repo.snapshot()["runs"] == []
    reopened = QuietService(service.repo.root)
    assert reopened.repo.snapshot()["selected_source_mode"] == "web"
    admitted = reopened.admit("질문", "question")
    assert admitted["source_mode"] == "web"
    assert reopened.repo.snapshot()["runs"][0]["source_mode"] == "web"


def test_자료잠금은_무부작용_동일자료는_새버전_다른자료큐는_캡처(service):
    first = service.admit("로컬 질문", "local")
    before = saved(service)
    for text in ("/웹", "/웹 질문"):
        with pytest.raises(ApiError) as e:
            service.admit(text, text)
        assert (e.value.status, e.value.code) == (409, "source_mode_locked")
        assert saved(service) == before
    revised = service.admit("/DB 새 질문", "revised")
    assert revised["control_revision"] == first["control_revision"] + 1
    queued = service.admit("/큐 /웹 나중 질문", "queue")
    assert queued["source_mode"] == "web"
    snap = service.repo.snapshot()
    assert snap["selected_source_mode"] == "local"
    assert snap["queue"][0]["source_mode"] == "web"
    service.cancel(first["run_id"], revised["control_revision"], "cancel")
    service.admit("/DB", "mode-only")
    assert service.repo.snapshot()["queue_paused"]
    service.continue_queue("continue")
    assert service.repo.snapshot()["selected_source_mode"] == "web"
    assert service.repo.snapshot()["runs"][-1]["source_mode"] == "web"


def test_재전송은_최초자료사용_잠금보다먼저_전역선택불변(service):
    first = service.admit("첫 질문", "first")
    service.cancel(first["run_id"], 1, "cancel")
    newer = service.admit("/웹 다른 질문", "new")
    before = saved(service)
    assert service.admit("첫 질문", "first") == first
    assert saved(service) == before
    with pytest.raises(ApiError) as e:
        service.admit("첫 질문", "first", source_mode="web")
    assert e.value.code == "request_id_conflict"
    assert saved(service) == before
    assert newer["source_mode"] == service.repo.snapshot()["selected_source_mode"] == "web"


def test_사용불가자료는_무부작용(service):
    base = ExecutionService(service.repo.root / "base")
    base.spawn = lambda *args: None
    before = saved(base)
    with pytest.raises(ApiError) as e:
        base.admit("/웹", "unavailable")
    assert e.value.code == "source_mode_unavailable"
    assert saved(base) == before


def test_HTTP_누락허용_null과충돌거부_스냅샷(service):
    app = create_app(service.repo.root, service_factory=lambda _: service)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert (
            c.post(
                "/api/spike/commands", json={"text": "/웹", "client_request_id": "ok"}
            ).status_code
            == 202
        )
        before = saved(service)
        for value in (None, "other"):
            r = c.post(
                "/api/spike/commands",
                json={"text": "질문", "client_request_id": "bad", "source_mode": value},
            )
            assert r.status_code == 422
        r = c.post(
            "/api/spike/commands",
            json={"text": "/웹 질문", "client_request_id": "conflict", "source_mode": "local"},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "source_mode_conflict"
        assert saved(service) == before
        snap = c.get("/api/spike/snapshot").json()
        assert snap["source_modes"] == ["local", "web"] and snap["selected_source_mode"] == "web"


LEGACY_TEXTS = ["과거 질문", "/웹 공개 자료", "/웹", "/웹 /DB 충돌"]


def remember_legacy_request(service, text):
    """자료 모드 도입 전 저장 형식(body·response에 source_mode 없음)의 접수 기록."""
    legacy = {
        "instruction_id": "old-instruction",
        "run_id": "old-run",
        "control_revision": 1,
        "mode": "immediate",
        "status": "received",
    }
    with service.repo.transaction() as (db, _state):
        service.repo.remember(db, "legacy", {"action": "commands", "text": text}, legacy)


@pytest.mark.parametrize("text", LEGACY_TEXTS)
def test_과거응답_명시local_재전송도_새접두어문법으로_재해석하지않는다(service, text):
    service.admit("/웹 질문", "new")
    remember_legacy_request(service, text)
    before = saved(service)
    spawned = list(service.spawned)
    response = service.admit(text, "legacy", source_mode="local")
    assert response["source_mode"] == "local" and response["run_id"] == "old-run"
    assert service.spawned == spawned
    assert saved(service) == before


@pytest.mark.parametrize("text", LEGACY_TEXTS)
def test_과거응답_자료누락은_local_현재web선택과무관_원행불변(service, text, monkeypatch):
    service.admit("/웹 질문", "new")
    remember_legacy_request(service, text)
    before = saved(service)
    from contextlib import contextmanager

    statements = []
    transaction = service.repo.transaction

    @contextmanager
    def traced_transaction():
        with transaction() as (db, state):
            db.set_trace_callback(statements.append)
            yield db, state

    monkeypatch.setattr(service.repo, "transaction", traced_transaction)
    spawned = list(service.spawned)
    response = service.admit(text, "legacy")
    assert service.spawned == spawned
    assert not any(
        sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) for sql in statements
    )
    assert response.get("source_mode") == "local"
    assert response["run_id"] == "old-run"
    assert saved(service) == before
    with pytest.raises(ApiError) as e:
        service.admit(text, "legacy", source_mode="web")
    assert e.value.code == "request_id_conflict"
    assert saved(service) == before
    with pytest.raises(ApiError) as e:
        service.admit(text + " 다른 원문", "legacy")
    assert (e.value.status, e.value.code) == (409, "request_id_conflict")
    assert saved(service) == before


def test_동일ID의_명시자료불일치는_접두어충돌보다먼저409(service):
    service.admit("/웹 질문", "first")
    before = saved(service)
    with pytest.raises(ApiError) as e:
        service.admit("/웹 질문", "first", source_mode="local")
    assert (e.value.status, e.value.code) == (409, "request_id_conflict")
    assert saved(service) == before


def test_과거보고서와사건의_자료누락은_조회에서만local(service):
    first = service.admit("질문", "first")
    with service.repo.transaction() as (db, state):
        run = service.repo.run(state, first["run_id"])
        run["draft"] = "# 시험 보고서\n\n이전 정본 본문"
        service.publish(db, state, run["run_id"], 1)
        service.repo.save_revision(db, run)
        report_id = run["report_id"]
        row = db.execute("SELECT body FROM reports WHERE report_id=?", (report_id,)).fetchone()
        report = json.loads(row[0])
        report.pop("source_mode")
        db.execute("UPDATE reports SET body=? WHERE report_id=?", (json.dumps(report), report_id))
        row = db.execute("SELECT body FROM events ORDER BY seq LIMIT 1").fetchone()
        event = json.loads(row[0])
        event.pop("source_mode")
        db.execute(
            "UPDATE events SET body=? WHERE event_id=?", (json.dumps(event), event["event_id"])
        )
    before = saved(service)
    assert service.report_bytes(report_id)[0].get("source_mode") == "local"
    assert service.repo.get_event(event["event_id"]).get("source_mode") == "local"
    assert service.repo.archive(first["run_id"])["events"][0].get("source_mode") == "local"
    assert saved(service) == before


def test_재시작뒤_구성에서사라진_큐자료는_시작전무부작용409(service):
    first = service.admit("로컬 질문", "first")
    service.admit("/큐 /웹 예약", "queue")
    service.cancel(first["run_id"], 1, "cancel")
    local_only = ExecutionService(service.repo.root)
    local_only.spawn = lambda *args: None
    before = saved(local_only)
    with pytest.raises(ApiError) as e:
        local_only.continue_queue("continue")
    assert (e.value.status, e.value.code) == (409, "source_mode_unavailable")
    assert saved(local_only) == before


def test_완료뒤_모드선택은_과거실행아카이브에_혼입되지않음(service):
    first = service.admit("질문", "first")
    with service.repo.transaction() as (db, state):
        run = service.repo.run(state, first["run_id"])
        run["draft"] = "# 고정 실행\n\n이전 본문"
        service.publish(db, state, run["run_id"], 1)
        service.repo.save_revision(db, run)
    before = service.repo.archive(first["run_id"])
    service.admit("/웹", "switch")
    after = service.repo.archive(first["run_id"])
    assert after["run"]["source_mode"] == "local"
    assert after["events"] == before["events"]
    assert service.repo.snapshot()["selected_source_mode"] == "web"
