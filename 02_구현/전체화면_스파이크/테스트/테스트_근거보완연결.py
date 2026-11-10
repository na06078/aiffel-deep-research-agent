"""§29.18 실제 서비스/ASGI/SQLite, 모델 판단은 명시 대역이며 외부 전송 금지."""

import asyncio
import copy
import json

import httpx
import pytest

from 서버.실제연구제어 import RealResearchService
from 서버.앱 import create_app
from 테스트.테스트_보고서보완연결 import (
    forbid_network,  # noqa: F401 -- pytest autouse fixture 재사용
    seed_blocked,
    settle,
)


class EvidenceModel:
    def __init__(self):
        self.calls = []
        self.hold = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.invalid = False
        self.negative = False
        self.initial_type = "citation_gap"
        self.initial_count = 3

    async def complete(self, system, user, *, context, on_attempt, response_schema=None, **kwargs):
        self.calls.append(
            {"context": context, "user": user, "system": system, "schema": response_schema}
        )
        attempt = f"evidence-fake-{len(self.calls)}"
        on_attempt(attempt)
        operation = context["operation"]
        if self.hold == operation and context["revision"] == 2:
            self.entered.set()
            await self.release.wait()
        data = json.loads(user)
        if operation == "semantic_correction":
            reply = {
                "근거목록ID": data["evidence_set_id"],
                "교정": [
                    {
                        "대상번호": t["대상번호"],
                        "내용": t["기존주장"],
                        "근거구간": [
                            next(
                                e["id"]
                                for e in data["catalog"]
                                if e["source_id"] in t["allowed_source_ids"]
                            )
                        ],
                    }
                    for t in data["targets"]
                ],
            }
            if self.invalid:
                reply["교정"] = []
        elif operation == "semantic_review":
            negative = context["revision"] == 1 or self.negative
            problems = []
            if negative:
                for n in range(self.initial_count if context["revision"] == 1 else 1):
                    i = n % 3
                    problems.append(
                        {
                            "유형": self.initial_type
                            if context["revision"] == 1
                            else "citation_gap",
                            "설명": "선택 인용 구간에 보완이 필요하다.",
                            "본문발췌": f"사건{i + 1}"
                            + (
                                "이 후속 사건을 필연적으로 일으켰다."
                                if i == 0
                                else "는 다른 사건의 유일한 원인이다."
                            ),
                            "근거번호": [data["contexts"][0]["id"]],
                        }
                    )
            verdict = (
                "undetermined"
                if negative
                and self.initial_type == "insufficient_context"
                and context["revision"] == 1
                else "needs_revision"
                if negative
                else "supported"
            )
            reply = {
                "검토목록ID": data["review_set_id"],
                "판정": verdict,
                "문제": problems,
                "검토요약": "모델 의미판단이 아닌 전송/상태용 명시 대역",
            }
        else:
            raise AssertionError("research_rerun_forbidden: " + operation)
        return {"content": json.dumps(reply, ensure_ascii=False), "attempt_id": attempt}

    def usage(self):
        return {"attempt_count": len(self.calls)}

    async def aclose(self):
        pass


def seed(service):
    old, sections = seed_blocked(service)
    third = copy.deepcopy(sections[1])
    third.update(section_id="section-3", title="절3", order=3, assignee="researcher-3")
    third["본문"] = third["본문"].replace("사건2", "사건3").replace("절2", "절3")
    third["근거"][0]["주장"] = "사건3는 다른 사건의 유일한 원인이다."
    sections.append(third)
    for s in sections:
        s["본문"] = s["본문"].replace("«자료»\n", "«자료»\n\n")
    draft = (
        "# 보존할 제목\n\n머리말 보존\n\n"
        + "\n\n".join(s["본문"] for s in sections)
        + "\n\n## 맺음말\n맺음말 보존"
    )
    with service.repo.transaction() as (db, data):
        run = data["runs"][0]
        run.update(status="running", phase="evaluating", draft=draft)
        run.pop("semantic_review_state")
        for s in sections:
            service.repo.event(db, run["run_id"], 1, "research.completed", s["assignee"], output=s)
        service.repo.save_revision(db, run)
    return {
        "run_id": old["run_id"],
        "revision": 1,
        "title": old["report_title"],
        "question": old["question"],
        "results": sections,
        "source_mode": "local",
        "draft": draft,
    }


@pytest.fixture
async def setup_app(tmp_path):
    corpus = tmp_path / "합성코퍼스.json"
    corpus.write_text(
        json.dumps(
            {
                "docs": {
                    "자료": "자료에는 두 사건의 기록이 각각 남아 있다. 세 사건을 구별할 추가 기록이 보존되어 있다.",
                    "안읽은자료": "외부로 보내면 안 되는 미열람 문서.",
                },
                "links": {},
            }
        ),
        encoding="utf-8",
    )
    model = EvidenceModel()
    app = create_app(
        data_dir=tmp_path / "저널",
        static_dir=tmp_path / "none",
        service_factory=lambda path: RealResearchService(path, model, corpus),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        service = app.state.service
        state = seed(service)
        yield client, service, model, state


async def test_자동한번_세곳인용교체_두추가호출_원본보존(setup_app):
    client, service, model, state = setup_app
    await service.evaluate(state)
    await settle(service)
    archive = service.repo.archive(state["run_id"])
    run = archive["run"]
    assert [c["context"]["operation"] for c in model.calls] == [
        "semantic_review",
        "semantic_correction",
        "semantic_review",
    ]
    assert run["status"] == "completed" and run["report_id"]
    assert run["control_revision"] == 2 and run["actual_model_requests"] == 30
    assert run["correction_state"] == {
        "automatic": True,
        "mode": "evidence",
        "source_revision": 1,
        "revision": 2,
        "status": "completed",
        "max_model_calls": 2,
        "client_request_id": f"auto-evidence:{state['run_id']}:1",
        "passed": True,
    }
    original = archive["revisions"][0]
    assert original["status"] == "blocked" and original["phase"] == "quality_failed"
    assert original["quality"]["reasons"] == ["semantic_review_failed"]
    assert len(original["quality"]["semantic_review"]["문제"]) == 3
    assert original["draft"] == state["draft"] == run["draft"]
    assert run["visual_summary"] == original["visual_summary"]
    applied = next(
        e["output"] for e in archive["events"] if e["type"] == "report.correction.applied"
    )
    assert len(applied["changes"]) == 3
    assert all(
        e["인용문"]
        == "자료에는 두 사건의 기록이 각각 남아 있다. 세 사건을 구별할 추가 기록이 보존되어 있다."
        for s in applied["sections"]
        for e in s["근거"]
    )
    correction = model.calls[1]
    assert "안읽은자료" not in correction["user"]
    assert (
        sum(
            len(v.encode("utf-8"))
            for v in (
                correction["system"],
                correction["user"],
                json.dumps(correction["schema"], ensure_ascii=False),
            )
        )
        + 8192
        <= 272000
    )
    for _ in range(2):
        assert (await client.get(f"/api/spike/runs/{state['run_id']}/archive")).status_code == 200
    assert len(model.calls) == 3


async def test_전체55요청넘을추가예약은_자동소비전차단(setup_app):
    _client, service, model, state = setup_app
    with service.repo.transaction() as (db, data):
        data["runs"][0]["actual_model_requests"] = 53
        service.repo.save_revision(db, data["runs"][0])
    await service.evaluate(state)
    await settle(service)
    run = service.repo.archive(state["run_id"])["run"]
    assert [c["context"]["operation"] for c in model.calls] == ["semantic_review"]
    assert run["actual_model_requests"] == 54
    assert run["status"] == "blocked" and run["report_id"] is None
    assert "correction_state" not in run and run["control_revision"] == 1


@pytest.mark.parametrize(
    "case,extra,status", [("invalid", 1, "failed"), ("negative", 2, "blocked")]
)
async def test_불량보완과_유효재부정_출판과반복금지(setup_app, case, extra, status):
    client, service, model, state = setup_app
    model.invalid = case == "invalid"
    model.negative = case == "negative"
    await service.evaluate(state)
    await settle(service)
    archive = service.repo.archive(state["run_id"])
    run = archive["run"]
    assert run["status"] == status and run["report_id"] is None
    assert (
        run["correction_state"]["status"] == "failed" and run["correction_state"]["passed"] is False
    )
    assert len(model.calls) == 1 + extra
    assert archive["revisions"][0]["draft"] == state["draft"]
    if case == "negative":
        assert run["quality"]["reasons"] == ["semantic_review_failed"]
        assert run["quality"]["semantic_review"]["판정"] == "needs_revision"
    for _ in range(2):
        await client.get(f"/api/spike/runs/{state['run_id']}/archive")
        response = await client.post(
            f"/api/spike/runs/{state['run_id']}/correction",
            json={"control_revision": 2, "client_request_id": f"again-{_}"},
        )
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "correction_consumed"
        )
    assert len(model.calls) == 1 + extra and service.repo.snapshot()["queue_paused"]
    with service.repo.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


@pytest.mark.parametrize("hold", ["semantic_correction", "semantic_review"])
@pytest.mark.parametrize("action", ["intervene", "cancel", "delete", "restart"])
async def test_자동보완수명_fence_늦은출판금지(setup_app, hold, action):
    client, service, model, state = setup_app
    model.hold = hold
    await service.evaluate(state)
    await asyncio.wait_for(model.entered.wait(), 3)
    run_id = state["run_id"]
    if action == "intervene":
        with service.repo.transaction() as (db, data):
            service.start(db, data, "새 지시", "intervention")
        expected_revision = 3
    elif action == "restart":
        replacement_model = EvidenceModel()
        replacement = RealResearchService(
            service.repo.root, replacement_model, service.repo.root.parent / "합성코퍼스.json"
        )
        try:
            restored = replacement.repo.snapshot()["runs"][0]
            assert (
                restored["status"] == "interrupted"
                and restored["correction_state"]["status"] == "failed"
            )
            assert replacement_model.calls == []
        finally:
            await replacement.close()
        expected_revision = 2
    else:
        response = await client.post(
            "/api/spike/cancel",
            json={"run_id": run_id, "control_revision": 2, "client_request_id": "cancel"},
        )
        assert response.status_code == 200
        if action == "delete":
            response = await client.post(
                f"/api/spike/runs/{run_id}/delete",
                json={"confirm_run_id": run_id, "client_request_id": "delete"},
            )
            assert response.status_code == 200
        expected_revision = 2
    model.release.set()
    await settle(service)
    assert len(model.calls) == (2 if hold == "semantic_correction" else 3)
    with service.repo.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0
        if action == "delete":
            for table in ("revisions", "calls", "activity_spans"):
                assert (
                    db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)
                    ).fetchone()[0]
                    == 0
                )
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM events WHERE json_extract(body,'$.run_id')=?", (run_id,)
                ).fetchone()[0]
                == 0
            )
            assert service.repo.snapshot()["runs"] == []
        else:
            run = service.repo.snapshot()["runs"][0]
            assert run["control_revision"] == expected_revision and run["report_id"] is None
            assert run["correction_state"]["status"] == "failed"
            assert (
                run["status"]
                == {"intervene": "running", "cancel": "cancelled", "restart": "interrupted"}[action]
            )
            if action == "intervene":
                assert run["question"] == "새 지시" and run["draft"] is None


async def test_중복evaluate와수동경합_자동소비한번만(setup_app):
    from 서버.실제연구제어 import SupersededResearch

    client, service, model, state = setup_app
    model.hold = "semantic_correction"
    results = await asyncio.gather(
        service.evaluate(state), service.evaluate(state), return_exceptions=True
    )
    assert all(isinstance(r, (dict, SupersededResearch)) for r in results), results
    await asyncio.wait_for(model.entered.wait(), 3)
    response = await client.post(
        f"/api/spike/runs/{state['run_id']}/correction",
        json={"control_revision": 2, "client_request_id": "manual-race"},
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "correction_consumed"
    model.release.set()
    await settle(service)
    archive = service.repo.archive(state["run_id"])
    assert len([e for e in archive["events"] if e["type"] == "report.correction.requested"]) == 1
    assert len(model.calls) == 3 and archive["run"]["control_revision"] == 2


@pytest.mark.parametrize(
    "case,code",
    [
        ("unsupported", "correction_ineligible"),
        ("contradiction", "correction_ineligible"),
        ("undetermined", "correction_ineligible"),
        ("four", "correction_ineligible"),
        ("web", "correction_ineligible"),
        ("published", "correction_ineligible"),
        ("other_reason", "correction_ineligible"),
        ("consumed", "correction_consumed"),
        ("raw_mismatch", "invalid_target"),
        ("section_changed", "invalid_target"),
        ("source_changed", "invalid_target"),
        ("stale", "revision_conflict"),
        ("not_current", "revision_conflict"),
        ("legacy", "correction_ineligible"),
    ],
)
async def test_자동부적격_원래부정보존_추가요청0(setup_app, monkeypatch, case, code):
    from 서버.저장소 import ApiError

    _client, service, model, state = setup_app
    admit = service._request_correction
    attempts = []
    monkeypatch.setattr(service, "_request_correction", lambda *a, **kw: attempts.append((a, kw)))
    await service.evaluate(state)
    assert len(model.calls) == 1 and len(attempts) == 1
    monkeypatch.setattr(service, "_request_correction", admit)
    with service.repo.transaction() as (db, data):
        run = data["runs"][0]
        decision = run["quality"]["semantic_review"]
        if case in ("unsupported", "contradiction"):
            decision["문제"][0]["유형"] = case
        elif case == "undetermined":
            decision["판정"] = "undetermined"
        elif case == "four":
            decision["문제"].append(copy.deepcopy(decision["문제"][0]))
        elif case == "web":
            run["source_mode"] = "web"
        elif case == "published":
            run["report_id"] = "published"
        elif case == "other_reason":
            run["quality"]["reasons"].append("visual_integrity_error")
        elif case == "consumed":
            run["correction_state"] = {"status": "failed"}
        elif case == "raw_mismatch":
            run["semantic_review_state"]["raw"] = "{}"
        elif case == "section_changed":
            section = copy.deepcopy(state["results"][0])
            section["본문"] += " 변경"
            service.repo.event(
                db, run["run_id"], 1, "research.completed", "researcher-1", output=section
            )
        elif case == "source_changed":
            service.corpus.docs["자료"] += " 원문 변경"
        elif case == "not_current":
            data["current_run_id"] = None
        elif case == "legacy":
            decision.pop("검토계약")
        service.repo.save_revision(db, run)
    before = service.repo.path.read_bytes()
    with pytest.raises(ApiError) as exc:
        admit(state["run_id"], 9 if case == "stale" else 1, "ineligible", automatic=True)
    assert exc.value.code == code
    assert service.repo.path.read_bytes() == before and len(model.calls) == 1 and not service.tasks


@pytest.mark.parametrize(
    "kind,count,verdict",
    [
        ("unsupported", 1, "needs_revision"),
        ("contradiction", 1, "needs_revision"),
        ("insufficient_context", 1, "undetermined"),
        ("citation_gap", 4, "needs_revision"),
    ],
)
async def test_실제parse된다른부정판정은_자동요청0(setup_app, kind, count, verdict):
    _client, service, model, state = setup_app
    model.initial_type, model.initial_count = kind, count
    await service.evaluate(state)
    await settle(service)
    run = service.repo.archive(state["run_id"])["run"]
    assert run["quality"]["reasons"] == ["semantic_review_failed"]
    assert run["quality"]["semantic_review"]["판정"] == verdict
    assert len(run["quality"]["semantic_review"]["문제"]) == count
    assert (
        "correction_state" not in run and run["report_id"] is None and run["control_revision"] == 1
    )
    assert len(model.calls) == 1


async def test_보완응답대기중원문변경_재검토0_원본보존(setup_app):
    _client, service, model, state = setup_app
    model.hold = "semantic_correction"
    await service.evaluate(state)
    await asyncio.wait_for(model.entered.wait(), 3)
    service.corpus.docs["자료"] += " 원문 변경"
    model.release.set()
    await settle(service)
    archive = service.repo.archive(state["run_id"])
    assert archive["run"]["status"] == "failed" and archive["run"]["report_id"] is None
    assert archive["run"]["correction_state"]["status"] == "failed"
    assert archive["revisions"][0]["draft"] == state["draft"]
    assert len(model.calls) == 2
    assert not any(e["type"] == "report.correction.applied" for e in archive["events"])


async def test_자동새버전은_다른operation과세번째호출_영속게이트차단(setup_app):
    from 서버.교안연구 import ResearchContractError

    _client, service, model, state = setup_app
    model.hold = "semantic_review"
    await service.evaluate(state)
    await asyncio.wait_for(model.entered.wait(), 3)
    for operation in (
        "plan",
        "read_one",
        "research",
        "synthesize",
        "semantic_correction",
        "semantic_review",
    ):
        with pytest.raises(ResearchContractError, match="call budget or operation exceeded"):
            await service.complete(
                {"run_id": state["run_id"], "revision": 2}, "editor", operation, ("JSON", "JSON")
            )
    assert len(model.calls) == 3
    with service.repo.read_connection() as db:
        assert db.execute("SELECT COUNT(*) FROM calls WHERE revision=2").fetchone()[0] == 2
    model.release.set()
    await settle(service)


async def test_자동작업시작전close_소비실패보존_추가호출0(setup_app):
    _client, service, model, state = setup_app
    await service.evaluate(state)
    await service.close()
    run = service.repo.archive(state["run_id"])["run"]
    assert run["status"] == "interrupted" and run["correction_state"]["status"] == "failed"
    assert len(model.calls) == 1 and run["report_id"] is None


async def test_재검토합격전에는_기존큐재개금지(setup_app):
    _client, service, model, state = setup_app
    model.hold = "semantic_review"
    starts = []
    service.spawn = lambda *args: starts.append(args)
    with service.repo.transaction() as (_db, data):
        data["queue"] = [
            {"instruction_id": "queued", "text": "후속", "source_mode": "local", "status": "queued"}
        ]
    await service.evaluate(state)
    await asyncio.wait_for(model.entered.wait(), 3)
    assert service.repo.snapshot()["queue_paused"] and starts == []
    model.release.set()
    await settle(service)
    assert len(starts) == 1 and len(model.calls) == 3
    assert service.repo.archive(state["run_id"])["run"]["status"] == "completed"


async def test_전체55요청경계는_두추가호출후_통과가능(setup_app):
    _client, service, model, state = setup_app
    with service.repo.transaction() as (db, data):
        data["runs"][0]["actual_model_requests"] = 52
        service.repo.save_revision(db, data["runs"][0])
    await service.evaluate(state)
    await settle(service)
    run = service.repo.archive(state["run_id"])["run"]
    assert len(model.calls) == 3 and run["actual_model_requests"] == 55
    assert run["status"] == "completed" and run["report_id"]


@pytest.mark.parametrize("stage", ["original", "rereview"])
async def test_비정상검토는_내용실패와구별하고_자동반복금지(setup_app, monkeypatch, stage):
    _client, service, model, state = setup_app
    original_complete = model.complete

    async def malformed(*args, context, **kwargs):
        result = await original_complete(*args, context=context, **kwargs)
        revision = 1 if stage == "original" else 2
        if context["operation"] == "semantic_review" and context["revision"] == revision:
            result["content"] = "{}"
        return result

    monkeypatch.setattr(model, "complete", malformed)
    await service.evaluate(state)
    await settle(service)
    run = service.repo.archive(state["run_id"])["run"]
    assert run["quality"]["reasons"] == ["semantic_review_invalid"]
    assert run["status"] == "blocked" and run["report_id"] is None
    assert len(model.calls) == (1 if stage == "original" else 3)
    if stage == "original":
        assert "correction_state" not in run and run["control_revision"] == 1
    else:
        assert run["correction_state"]["status"] == "failed" and run["control_revision"] == 2


async def test_과거차단실행복원은_자동보완시작점이아님(setup_app, monkeypatch):
    client, service, model, state = setup_app
    monkeypatch.setattr(service, "_request_correction", lambda *a, **kw: None)
    await service.evaluate(state)
    original = service.repo.archive(state["run_id"])["run"]
    assert original["status"] == "blocked" and "correction_state" not in original
    replacement_model = EvidenceModel()
    replacement = RealResearchService(
        service.repo.root, replacement_model, service.repo.root.parent / "합성코퍼스.json"
    )
    try:
        assert replacement.repo.archive(state["run_id"])["run"] == original
        assert replacement_model.calls == [] and not replacement.tasks
        assert (await client.get(f"/api/spike/runs/{state['run_id']}/archive")).status_code == 200
        assert len(model.calls) == 1
    finally:
        await replacement.close()


async def test_실제SDK_근거보완strict_payload와예약상한_전송은MockTransport만(
    tmp_path, monkeypatch
):
    from 서버 import 실제모델
    from 서버.근거보완 import apply_evidence_correction, prepare_evidence_correction
    from 테스트.테스트_근거보완 import fixture
    from 테스트.테스트_근거보완 import response as correction_response
    from 테스트.테스트_실제모델 import model_for
    from 테스트.테스트_실제모델 import response as sdk_response

    def forbidden_auth_read(*args):
        raise AssertionError("auth_read_forbidden")

    monkeypatch.setattr(실제모델, "_project_key", forbidden_auth_read)
    draft, sections, problems, sources = fixture()
    request = prepare_evidence_correction(draft, sections, problems, sources)
    sent, reservations, callbacks = [], [], []

    def handler(http_request):
        sent.append(json.loads(http_request.content))
        assert callbacks and model.usage()["attempt_count"] == 1
        return httpx.Response(
            200,
            json=sdk_response(json.dumps(correction_response(request))),
            headers={"x-request-id": "fake-sdk-only"},
        )

    model = model_for(tmp_path, handler)
    model.client._custom_headers = {}
    reserve = model.budget.reserve

    def recorded_reserve(input_bound, output_bound, context):
        reservations.append((input_bound, output_bound, context))
        return reserve(input_bound, output_bound, context)

    monkeypatch.setattr(model.budget, "reserve", recorded_reserve)
    context = {
        "run_id": "fixture-only",
        "revision": 2,
        "actor": "editor",
        "operation": "semantic_correction",
    }
    try:
        raw = await model.complete(
            *request.prompts,
            context=context,
            max_output_tokens=4096,
            json_mode=True,
            response_schema=request.response_schema,
            on_attempt=callbacks.append,
        )
        result = apply_evidence_correction(raw["content"], request, sources)
        assert result["draft"] == draft
        assert len(sent) == len(callbacks) == len(reservations) == 1
        assert sent[0]["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "grounded_section",
                "strict": True,
                "schema": request.response_schema,
            },
        }
        assert sent[0]["messages"] == [
            {"role": "system", "content": request.prompts[0]},
            {"role": "user", "content": request.prompts[1]},
        ]
        assert sent[0]["max_completion_tokens"] == 4096 and sent[0]["reasoning_effort"] == "low"
        assert sent[0]["model"] == "gpt-5.6-luna" and "temperature" not in sent[0]
        expected_bound = (
            sum(
                len(t.encode("utf-8"))
                for t in (*request.prompts, json.dumps(request.response_schema, ensure_ascii=False))
            )
            + 8192
        )
        assert reservations == [(expected_bound, 4096, context)] and expected_bound <= 272000
        assert model.usage()["attempt_count"] == 1 and model.usage()["reserved_usd"] == 0
    finally:
        await model.aclose()
