"""§29.11 실제 서비스/ASGI/SQLite와 명시 모델 대역. 외부 전송 금지."""

import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

from 서버.보고서품질 import plain_markdown
from 서버.실제연구제어 import RealResearchService
from 서버.앱 import create_app

ROOT = Path(__file__).resolve().parents[3]
SCRATCH = ROOT / ".cache/보고서품질_검증/실모델점검/교정_연결"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import socket

    import httpcore

    def deny(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    async def deny_async(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", deny_async)


class CorrectionModel:
    def __init__(self):
        self.calls = []
        self.hold = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.invalid = False
        self.negative = False
        self.error = None

    async def complete(self, system, user, *, context, on_attempt, response_schema=None, **kwargs):
        self.calls.append({"context": context, "user": user, "schema": response_schema})
        attempt = f"isolated-{len(self.calls)}"
        on_attempt(attempt)
        operation = context["operation"]
        if self.hold == operation:
            self.entered.set()
            await self.release.wait()
        if self.error:
            raise self.error
        if operation == "semantic_correction":
            targets = json.loads(user)
            content = {"교정": [{"대상번호": item["대상번호"], "내용": f"자료에는 사건{item['대상번호']}의 기록이 있다."} for item in targets]}
            if self.invalid:
                content = {"교정": []}
        elif operation == "semantic_review":
            content = {"통과": not self.negative, "문제": [], "검토요약": "명시 대역 판정"}
            if self.negative:
                content["문제"] = [{"유형": "unsupported_claim", "설명": "대역의 명시 불합격", "본문발췌": "자료에는 사건1의 기록이 있다.", "출처": ["자료"]}]
        else:
            raise AssertionError("research_rerun_forbidden: " + operation)
        return {"content": json.dumps(content, ensure_ascii=False), "attempt_id": attempt}

    def usage(self):
        return {"attempt_count": len(self.calls)}

    async def aclose(self):
        pass


def seed_blocked(service):
    sections = []
    claims = ["사건1이 후속 사건을 필연적으로 일으켰다.", "사건2는 다른 사건의 유일한 원인이다."]
    for i, claim in enumerate(claims, 1):
        sections.append({"section_id": f"section-{i}", "title": f"절{i}", "order": i, "round": 2,
                         "assignee": f"researcher-{i}", "role": "시험 역할", "충분": True, "부족": "",
                         "본문": plain_markdown(claim) + " «자료»\n" + (f"절{i}의 기존 문장과 서식을 그대로 보존하는 검증용 설명이다. " * 45),
                         "근거": [{"주장": claim, "출처": "자료", "인용문": "자료에는 두 사건의 기록이 각각 남아 있다."}],
                         "읽은문서": ["자료"], "메모": [["자료", "기존 읽기 보존"]]})
    problems = [{"유형": "unsupported_claim", "설명": "근거 없는 인과", "본문발췌": claim, "출처": ["자료"]} for claim in claims]
    draft = "# 보존할 제목\n\n머리말 보존\n\n" + "\n\n".join(s["본문"] for s in sections) + "\n\n## 맺음말\n맺음말 보존"
    with service.repo.transaction() as (db, data):
        run, _ = service.start(db, data, "원래 질문", "seed")
        run.update(status="blocked", phase="quality_failed", draft=draft, report_title="보존할 제목", actual_model_requests=27,
                   visual_summary={"머리말": "머리말 보존", "맺음말": "맺음말 보존", "표": [], "도식": [], "시각화생략사유": "시험"},
                   quality={"passed": False, "reasons": ["semantic_review_failed"], "semantic_review": {"통과": False, "문제": problems, "검토요약": "인과 두 건"}},
                   semantic_review_state={"revision": 1, "status": "completed", "raw": json.dumps({"통과": False, "문제": problems, "검토요약": "인과 두 건"}, ensure_ascii=False)})
        for section in sections:
            service.repo.event(db, run["run_id"], 1, "research.completed", section["assignee"], output=section)
        service.repo.save_revision(db, run)
        data.update(queue_paused=True, queue_pause_reason="quality_failed")
    return copy.deepcopy(run), sections


@pytest.fixture
async def setup_app(tmp_path):
    corpus_path = tmp_path / "합성코퍼스.json"
    corpus_path.write_text(json.dumps({"docs": {"자료": "자료에는 두 사건의 기록이 각각 남아 있다.", "안읽은자료": "호출 입력에 노출하지 않는다."}, "links": {}}), encoding="utf-8")
    model = CorrectionModel()
    app = create_app(data_dir=tmp_path / "저널", static_dir=tmp_path / "no-static",
                     service_factory=lambda path: RealResearchService(path, model, corpus_path))
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        old, sections = seed_blocked(app.state.service)
        yield client, app.state.service, model, old, sections


async def settle(service):
    while service.tasks:
        await asyncio.gather(*list(service.tasks))


async def post(client, old, request_id="correction", revision=1):
    return await client.post(f"/api/spike/runs/{old['run_id']}/correction", json={"control_revision": revision, "client_request_id": request_id})


async def test_명시접수_두호출_기존버전보존_멱등성(setup_app):
    client, service, model, old, sections = setup_app
    response = await post(client, old)
    assert response.status_code == 202, response.text
    admission = {"run_id": old["run_id"], "control_revision": 2, "source_revision": 1, "status": "running", "max_model_calls": 2}
    assert response.json() == admission
    assert (await post(client, old)).json() == admission
    await settle(service)
    archive = (await client.get(f"/api/spike/runs/{old['run_id']}/archive")).json()
    assert archive["revisions"][0] == old
    run = archive["run"]
    assert run["status"] == "completed", run
    assert run["actual_model_requests"] == 29
    assert run["correction_state"]["status"] == "completed"
    assert run["correction_state"]["passed"] is True
    assert [c["context"]["operation"] for c in model.calls] == ["semantic_correction", "semantic_review"]
    assert model.calls[0]["schema"] is not None
    assert "안읽은자료" not in model.calls[0]["user"]
    event = next(e for e in archive["events"] if e["type"] == "report.correction.applied")
    assert event["output"]["source_revision"] == 1
    assert len(event["output"]["changes"]) == 2
    expected = old["draft"]
    for i, section in enumerate(sections, 1):
        expected = expected.replace(section["근거"][0]["주장"].replace(".", "\\."), f"자료에는 사건{i}의 기록이 있다\\.")
    assert run["draft"] == expected
    assert (await post(client, old)).json() == admission
    assert len(model.calls) == 2


@pytest.mark.parametrize("hold", ["semantic_correction", "semantic_review"])
async def test_즉시개입은_보완소비상태를_실패로_고정하고_늦은출판차단(setup_app, hold):
    client, service, model, old, _ = setup_app
    model.hold = hold
    assert (await post(client, old)).status_code == 202
    await asyncio.wait_for(model.entered.wait(), 3)
    # 정상 즉시개입과 같은 start 경계. 새 연구 spawn은 이 대역 범위 밖이다.
    with service.repo.transaction() as (db, data):
        service.start(db, data, "새 지시", "intervention")
    snap = service.repo.snapshot()
    assert snap["runs"][0]["correction_state"]["status"] == "failed"
    model.release.set()
    await settle(service)
    run = service.repo.snapshot()["runs"][0]
    assert run["control_revision"] == 3 and run["draft"] is None
    assert run["report_id"] is None and run["question"] == "새 지시"
    assert len(model.calls) == (1 if hold == "semantic_correction" else 2)


@pytest.mark.parametrize("case,code", [("web", "correction_ineligible"), ("cancelled", "correction_ineligible"),
    ("published", "correction_ineligible"), ("invalid_review", "correction_ineligible"),
    ("zero", "correction_ineligible"), ("three", "correction_ineligible"),
    ("wrong_revision", "revision_conflict"), ("not_current", "revision_conflict"),
    ("ambiguous", "invalid_target"), ("consumed", "correction_consumed"), ("missing", "not_found")])
async def test_부적격은_호출과_버전쓰기전_정확한사유거절(setup_app, case, code):
    client, service, model, old, _ = setup_app
    with service.repo.transaction() as (db, data):
        run = data["runs"][0]
        if case == "web":
            run["source_mode"] = "web"
        elif case == "cancelled":
            run["status"] = "cancelled"
        elif case == "published":
            run["report_id"] = "already-published"
        elif case == "invalid_review":
            run["quality"]["reasons"] = ["semantic_review_invalid"]
        elif case == "zero":
            run["quality"]["semantic_review"]["문제"] = []
        elif case == "three":
            run["quality"]["semantic_review"]["문제"].append(copy.deepcopy(run["quality"]["semantic_review"]["문제"][0]))
        elif case == "not_current":
            data["current_run_id"] = None
        elif case == "ambiguous":
            run["draft"] += "\n\n" + run["draft"].splitlines()[4]
        elif case == "consumed":
            run["correction_state"] = {"status": "failed"}
        elif case == "missing":
            data["runs"] = []
        service.repo.save_revision(db, run)
    before = service.repo.path.read_bytes()
    response = await post(client, old, revision=9 if case == "wrong_revision" else 1)
    assert response.status_code == (404 if case == "missing" else 409)
    assert response.json()["error"]["code"] == code
    assert service.repo.path.read_bytes() == before
    assert model.calls == [] and not service.tasks


@pytest.mark.parametrize("field,value", [("control_revision", True), ("control_revision", "1"), ("control_revision", 0),
                                         ("client_request_id", ""), ("client_request_id", "x" * 201), ("extra", 1)])
async def test_엄격요청검증_부작용없음(setup_app, field, value):
    client, service, model, old, _ = setup_app
    before = service.repo.path.read_bytes()
    body = {"control_revision": 1, "client_request_id": "strict", field: value}
    response = await client.post(f"/api/spike/runs/{old['run_id']}/correction", json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_request"
    assert service.repo.path.read_bytes() == before and not model.calls


async def test_다른ID재소비와_동일ID다른본문_원자거절(setup_app):
    client, service, model, old, _ = setup_app
    model.hold = "semantic_correction"
    admission = (await post(client, old)).json()
    await asyncio.wait_for(model.entered.wait(), 3)
    before = service.repo.path.read_bytes()
    response = await post(client, old, "different", revision=2)
    assert response.status_code == 409 and response.json()["error"]["code"] == "correction_consumed"
    conflict = await post(client, old, revision=2)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "request_id_conflict"
    assert (await post(client, old)).json() == admission
    assert service.repo.path.read_bytes() == before and len(model.calls) == 1
    model.release.set()
    await settle(service)


@pytest.mark.parametrize("case,calls,status", [("invalid", 1, "failed"), ("negative", 2, "blocked"), ("exception", 1, "failed"), ("source_changed", 1, "failed"), ("static", 1, "blocked")])
async def test_보완실패는_예산소비_원본보존_재시도금지(setup_app, case, calls, status):
    client, service, model, old, _ = setup_app
    model.invalid = case == "invalid"
    model.negative = case == "negative"
    model.error = RuntimeError("SECRET-RAW-provider-stack") if case == "exception" else None
    if case == "static":
        with service.repo.transaction() as (db, data):
            data["runs"][0]["visual_summary"] = {}
            service.repo.save_revision(db, data["runs"][0])
        old["visual_summary"] = {}
    if case == "source_changed":
        model.hold = "semantic_correction"
    assert (await post(client, old)).status_code == 202
    if case == "source_changed":
        await asyncio.wait_for(model.entered.wait(), 3)
        service.corpus.docs["자료"] += " 버전 변경"
        model.release.set()
    await settle(service)
    archive = service.repo.archive(old["run_id"])
    run = archive["run"]
    assert run["status"] == status and run["report_id"] is None
    assert run["correction_state"]["status"] == "failed" and run["correction_state"]["passed"] is False
    assert archive["revisions"][0] == old
    assert len(model.calls) == calls
    if case == "negative":
        assert run["quality"]["reasons"] == ["semantic_review_failed"]
    if case == "static":
        assert "visual_integrity_error" in run["quality"]["reasons"]
    assert "SECRET-RAW" not in json.dumps(archive)
    again = await post(client, old, "again", revision=2)
    assert again.status_code == 409 and again.json()["error"]["code"] == "correction_consumed"
    assert len(model.calls) == calls and service.repo.snapshot()["queue_paused"]


@pytest.mark.parametrize("hold", ["semantic_correction", "semantic_review"])
@pytest.mark.parametrize("action", ["cancel", "delete"])
async def test_취소삭제_늦은응답_상태부활금지(setup_app, hold, action):
    client, service, model, old, _ = setup_app
    model.hold = hold
    assert (await post(client, old)).status_code == 202
    await asyncio.wait_for(model.entered.wait(), 3)
    response = await client.post("/api/spike/cancel", json={"run_id": old["run_id"], "control_revision": 2, "client_request_id": "cancel"})
    assert response.status_code == 200
    if action == "delete":
        response = await client.post(f"/api/spike/runs/{old['run_id']}/delete", json={"confirm_run_id": old["run_id"], "client_request_id": "delete"})
        assert response.status_code == 200, response.text
    model.release.set()
    await settle(service)
    assert len(model.calls) == (1 if hold == "semantic_correction" else 2)
    with service.repo.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0
        if action == "delete":
            for table in ("revisions", "calls", "activity_spans"):
                assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (old["run_id"],)).fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM events WHERE json_extract(body,'$.run_id')=?", (old["run_id"],)).fetchone()[0] == 0
            assert service.repo.snapshot()["runs"] == []
        else:
            run = service.repo.snapshot()["runs"][0]
            assert run["status"] == "cancelled" and run["correction_state"]["status"] == "failed"


async def test_프로세스복원은_미완료보완을_실패로_남기며_호출하지않음(setup_app):
    client, service, model, old, _ = setup_app
    model.hold = "semantic_correction"
    assert (await post(client, old)).status_code == 202
    await asyncio.wait_for(model.entered.wait(), 3)
    # 앱 비정상 종료 후 repo.restore의 동작을 새 서비스로 검증한다.
    replacement = RealResearchService(service.repo.root, CorrectionModel(), service.repo.root.parent / "합성코퍼스.json")
    try:
        run = replacement.repo.snapshot()["runs"][0]
        assert run["status"] == "interrupted"
        assert run["correction_state"]["status"] == "failed"
        assert replacement.model.calls == []
    finally:
        await replacement.close()
    model.release.set()
    await settle(service)


async def test_보완은_성공출판뒤에만_기존큐정책으로_넘긴다(setup_app):
    client, service, model, old, _ = setup_app
    model.hold = "semantic_review"
    queued_starts = []
    service.spawn = lambda *args: queued_starts.append(args)
    with service.repo.transaction() as (_db, data):
        data["queue"] = [{"instruction_id": "queued", "text": "후속", "source_mode": "local", "status": "queued"}]
    assert (await post(client, old)).status_code == 202
    await asyncio.wait_for(model.entered.wait(), 3)
    assert service.repo.snapshot()["queue_paused"] and queued_starts == []
    model.release.set()
    await settle(service)
    assert len(queued_starts) == 1
    assert service.repo.archive(old["run_id"])["run"]["status"] == "completed"
    assert len(model.calls) == 2


@pytest.mark.parametrize("case", ["missing_revision", "stale_review"])
async def test_누락된원본버전과_이전의미판정은_소비전거절(setup_app, case):
    client, service, model, old, _ = setup_app
    with service.repo.transaction() as (db, data):
        if case == "missing_revision":
            db.execute("DELETE FROM revisions WHERE run_id=?", (old["run_id"],))
        else:
            data["runs"][0]["semantic_review_state"]["revision"] = 99
            service.repo.save_revision(db, data["runs"][0])
    before = service.repo.path.read_bytes()
    response = await post(client, old)
    assert response.status_code == 409 and response.json()["error"]["code"] == "invalid_target"
    assert service.repo.path.read_bytes() == before and not model.calls


async def test_시작전_종료도_소비된보완을_실패로남긴다(setup_app):
    _client, service, model, old, _ = setup_app
    # 동기 메서드 바로 다음 close로 coroutine의 첫 실행 이전 취소를 재현한다.
    service.request_correction(old["run_id"], 1, "immediate-close")
    await service.close()
    run = service.repo.snapshot()["runs"][0]
    assert run["correction_state"]["status"] == "failed"
    assert run["status"] == "interrupted" and model.calls == []


async def test_세번째논리호출과_다른작업은_SDK전차단(setup_app):
    from 서버.교안연구 import ResearchContractError

    client, service, model, old, _ = setup_app
    model.hold = "semantic_review"
    assert (await post(client, old)).status_code == 202
    await asyncio.wait_for(model.entered.wait(), 3)
    for operation in ("plan", "semantic_review", "semantic_correction"):
        with pytest.raises(ResearchContractError, match="call budget or operation exceeded"):
            await service.complete({"run_id": old["run_id"], "revision": 2}, "editor", operation, ("JSON", "JSON"))
    assert len(model.calls) == 2
    with service.repo.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM calls WHERE revision=2").fetchone()[0] == 2
    model.release.set()
    await settle(service)


async def test_절은_현재원본버전의_가장높은라운드만_복원한다(setup_app):
    client, service, model, old, sections = setup_app
    obsolete = copy.deepcopy(sections[0])
    obsolete.update(round=1, 본문="이전 라운드는 교정에 전달하면 안 된다.", 근거=[])
    with service.repo.transaction() as (db, _data):
        service.repo.event(db, old["run_id"], 1, "research.completed", "researcher-1", output=obsolete)
        service.repo.event(db, old["run_id"], 77, "research.completed", "researcher-1", output=obsolete)
    assert (await post(client, old)).status_code == 202
    await settle(service)
    assert service.repo.archive(old["run_id"])["run"]["status"] == "completed"
    assert len(model.calls) == 2 and "이전 라운드" not in model.calls[0]["user"]


async def test_기본대역서비스는_보완API_지원없음(tmp_path):
    app = create_app(data_dir=tmp_path / "base", static_dir=tmp_path / "none")
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        before = app.state.service.repo.path.read_bytes()
        response = await client.post("/api/spike/runs/none/correction", json={"control_revision": 1, "client_request_id": "unsupported"})
        assert response.status_code == 409 and response.json()["error"]["code"] == "correction_unavailable"
        assert app.state.service.repo.path.read_bytes() == before and not app.state.service.tasks


async def test_불명사용량_제공자실패_안전메타데이터유지(setup_app):
    class UnknownFailure(Exception):
        code = "usage_unknown"
        safe_message = "사용량 확정 불가로 중단했습니다."
        attempt_id = "isolated-1"

    client, service, model, old, _ = setup_app
    model.error = UnknownFailure("SECRET-RAW-pending-usage")
    assert (await post(client, old)).status_code == 202
    await settle(service)
    archive = service.repo.archive(old["run_id"])
    run = archive["run"]
    assert run["failure"]["code"] == "usage_unknown"
    assert run["correction_state"]["status"] == "failed" and len(model.calls) == 1
    assert archive["revisions"][0]["actual_model_requests"] == 27
    assert run["actual_model_requests"] == 28
    assert service.repo.snapshot()["queue_pause_reason"] == "usage_unknown"
    assert "SECRET-RAW" not in json.dumps(archive)