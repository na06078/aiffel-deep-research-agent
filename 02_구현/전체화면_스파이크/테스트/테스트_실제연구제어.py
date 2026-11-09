"""주입 대역 + 실제 LangGraph/SQLite/코퍼스 검사; 실제 제공자 호출 아님."""

import asyncio
import json
import os
from pathlib import Path

import pytest

from 서버.실제연구제어 import RealResearchService

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
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


ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "01_참고자료/원본데이터/corpus.json"
EVIDENCE = ROOT / ".cache/보고서품질_검증/생성/로컬"


class ScriptedModel:
    """명시적 응답 대역. 전송 콜백/반환 계약만 재현하며 역사적 품질을 증명하지 않는다."""

    def __init__(self):
        self.calls = []
        self.closed = False
        self.active = 0
        self.max_active = 0
        self.section_calls = {}
        self.bodies = {}
        self.docs = json.loads(CORPUS.read_text("utf-8"))["docs"]
        self.titles = list(self.docs)

    async def complete(
        self, system, user, *, context, max_output_tokens=4096, json_mode=False, on_attempt=None
    ):
        actor, operation = context["actor"], context["operation"]
        self.calls.append((system, user, context))
        attempt_id = f"mock-attempt-{len(self.calls)}"
        on_attempt(attempt_id)
        self.active += 1
        self.max_active = max(self.active, self.max_active)
        await asyncio.sleep(0.003)
        self.active -= 1
        if operation == "plan":
            value = {
                "제목": "1894년 사건과 원인",
                "목차": [
                    {
                        "절": f"사건 {i}",
                        "지시": "사건과 원인을 자료대로 밝히라",
                        "역할": "원인 담당",
                        "시작문서": t,
                    }
                    for i, t in enumerate(self.titles[:4])
                ],
            }
        elif operation == "select":
            value = {"문서": None}
        elif operation == "read_one":
            value = "자료에 기재된 사건과 원인의 요약 메모."
        elif operation == "draft_section":
            n = self.section_calls.get(actor, 0) + 1
            self.section_calls[actor] = n
            title = self.titles[int(actor[-1]) - 1]
            body = (
                "자료에 근거한 연구 대역 문장으로 사실 검증 완료를 의미하지 않는다. " * 20
            ) + f"«{title}»"
            self.bodies[actor] = body
            value = {
                "본문": body,
                "충분": actor != "researcher-1" or n >= 2,
                "부족": "추가 원인 자료 필요" if actor == "researcher-1" and n == 1 else "",
                "근거": [
                    {
                        "주장": "자료에 근거한 연구 대역 문장",
                        "출처": title,
                        "인용문": self.docs[title][:80],
                    }
                ],
            }
        elif operation == "synthesize":
            value = {
                "머리말": "질문에 관한 연구를 정리한다.",
                "맺음말": "각 사건의 원인은 자료의 범위 안에서 해석해야 한다.",
                "표": [],
                "도식": [],
                "시각화생략사유": "이 대역은 시각 요약을 만들지 않는다.",
            }
        elif operation == "semantic_review":
            value = {
                "통과": True,
                "문제": [],
                "검토요약": "명시적 대역 판정이며 실제 의미 판별 검증 아님",
            }
        else:
            raise AssertionError(operation)
        return {
            "content": json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value,
            "model": "explicit-test-model",
            "attempt_id": attempt_id,
            "request_id": "mock-request",
            "usage": {"input_tokens": 11, "output_tokens": 13, "reasoning_tokens": 0},
            "estimated_cost_usd": 0,
        }

    def usage(self):
        return {
            "attempt_count": len(self.calls),
            "max_attempts": 40,
            "remaining_attempts": 40 - len(self.calls),
            "spent_usd": 0,
            "reserved_usd": 0,
            "max_usd": 1,
            "input_tokens": 11 * len(self.calls),
            "output_tokens": 13 * len(self.calls),
            "unknown_count": 0,
        }

    async def aclose(self):
        self.closed = True


async def settle(service):
    for _ in range(1000):
        if not service.tasks:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("service did not settle")


@pytest.fixture
def data_dir():
    import uuid

    target = EVIDENCE / uuid.uuid4().hex
    target.mkdir(parents=True)
    return target


async def test_실제6노드_Send병렬_부족절만2라운드_원고보존과_정본재열기(data_dir):
    model = ScriptedModel()
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("1894년에 일어난 주요 사건들은 무엇이고 각각의 원인은 무엇인가?", "first")
        await settle(service)
        snap = service.repo.snapshot()
        run = snap["runs"][0]
        assert run["status"] == "completed", run
        assert run["mock_requests"] == 0
        assert run["actual_model_requests"] == len(model.calls) > 0
        assert model.max_active >= 4
        assert model.section_calls == {
            "researcher-1": 2,
            "researcher-2": 1,
            "researcher-3": 1,
            "researcher-4": 1,
        }
        report, content, filename = service.report_bytes(run["report_id"])
        assert report["title"] == "1894년 사건과 원인"
        assert filename.startswith("🧾 실제 연구 보고서_")
        assert (data_dir / filename).read_bytes() == content == report["markdown"].encode("utf-8")
        for body in model.bodies.values():
            assert body in report["markdown"]
        archive = service.repo.archive(run["run_id"])
        assert {x["label"] for x in archive["spans"]} >= {
            "plan",
            "dispatch",
            "research",
            "review",
            "synthesize",
            "evaluate",
            "publish",
        }
        assert [e for e in archive["events"] if e["type"] == "review.completed"][-1]["output"][
            "stop_reason"
        ] == "sufficient"
        assert all(e["provider"] == "openai" for e in archive["events"])
        synth = next(x for x in model.calls if x[2]["operation"] == "synthesize")
        assert all(body in synth[1] for body in model.bodies.values())
        reviews = [c for c in model.calls if c[2]["operation"] == "semantic_review"]
        assert len(reviews) == 1
        assert report["markdown"] in reviews[0][1]
        assert run["quality"]["semantic_review"]["통과"] is True
        semantic_started = [
            e
            for e in archive["events"]
            if e["type"] == "model.started"
            and e["input"]["context"]["operation"] == "semantic_review"
        ]
        assert len(semantic_started) == 1 and semantic_started[0]["actor"] == "evaluator"
        review_call_id = semantic_started[0]["output"]["call_id"]
        waits = [
            s
            for s in archive["spans"]
            if s["kind"] == "model_wait" and s.get("call_id") == review_call_id
        ]
        assert len(waits) == 1 and waits[0]["status"] == "completed"
        assert semantic_started[0]["event_id"] in waits[0]["event_ids"]
    finally:
        await service.close()
    assert model.closed


class ExplorationModel(ScriptedModel):
    async def complete(self, *args, **kwargs):
        response = await super().complete(*args, **kwargs)
        operation = kwargs["context"]["operation"]
        if operation == "select":
            # 제시된 후보 안에서만 고르는 명시적 대역; 실제 LLM 선택 능력 검증 아님.
            candidates = args[1].split("[후보] ", 1)[1].split(", ")
            response["content"] = json.dumps({"문서": candidates[0]}, ensure_ascii=False)
        elif operation == "draft_section":
            obj = json.loads(response["content"])
            if kwargs["context"]["actor"] == "researcher-1":
                obj.update(충분=False, 부족="근거 부족 지속")
            response["content"] = json.dumps(obj, ensure_ascii=False)
        return response


async def test_라운드마다3건_최대2회_이전메모_유지와_정확한소진사유(data_dir):
    model = ExplorationModel()
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("사건과 원인", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "completed"
        assert run["research_stop_reason"] == "round_exhausted"
        sections = [
            e["output"]
            for e in service.repo.archive(run["run_id"])["events"]
            if e["type"] == "research.completed"
        ]
        one = [s for s in sections if s["assignee"] == "researcher-1"]
        assert [len(s["읽은문서"]) for s in one] == [3, 6]
        assert one[1]["메모"][:3] == one[0]["메모"]
        assert len(set(one[1]["읽은문서"])) == 6
        assert one[1]["인용"] == [model.titles[0]]
        assert one[1]["허위인용"] == []
        for section in sections:
            assert len(section["읽은문서"]) <= 3 * section["round"]
            other_seeds = set(model.titles[:4]) - {section["seed"]}
            assert not (set(section["읽은문서"]) & other_seeds)
        assert model.section_calls == {
            "researcher-1": 2,
            "researcher-2": 1,
            "researcher-3": 1,
            "researcher-4": 1,
        }
        for system, user, context in model.calls:
            if context["operation"] == "read_one":
                title = user.split("[문서: ", 1)[1].split("]", 1)[0]
                assert user == f"[문서: {title}]\n{service.corpus.docs[title]}"
        reads = {d for section in sections for d in section["읽은문서"]}
        report = service.report_bytes(run["report_id"])[0]["markdown"]
        assert all(
            f"[{title}](https://" not in report for title in set(service.corpus.docs) - reads
        )
    finally:
        await service.close()


class AlteredModel(ScriptedModel):
    def __init__(self, operation, mutate):
        super().__init__()
        self.operation, self.mutate = operation, mutate

    async def complete(self, *args, **kwargs):
        response = await super().complete(*args, **kwargs)
        if kwargs["context"]["operation"] == self.operation:
            response = self.mutate(response)
        return response


async def test_신규실제경로는_누락근거를_충분으로_승격하지_않는다(data_dir):
    def missing(response):
        obj = json.loads(response["content"])
        obj.pop("근거", None)
        return {**response, "content": json.dumps(obj, ensure_ascii=False)}

    model = AlteredModel("draft_section", missing)
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "failed"
        assert run["report_id"] is None
        assert not any(c[2]["operation"] == "synthesize" for c in model.calls)
        errors = [
            e["error"]["message"]
            for e in service.repo.archive(run["run_id"])["events"]
            if e["error"]
        ]
        assert any("evidence" in message for message in errors)
    finally:
        await service.close()


async def test_종합한번으로_표와SVG를_조립하고_절본문을_보존한다(data_dir):
    def visuals(response):
        obj = json.loads(response["content"])
        obj.update(
            표=[
                {
                    "제목": "대역 표",
                    "열": ["항목", "값"],
                    "행": [{"셀": ["A|B", "날짜\n미확인"], "출처": [model.titles[0]]}],
                }
            ],
            도식=[
                {
                    "제목": "대역 도식",
                    "설명": "실제 의미 판별 검증 아님",
                    "코드": '<svg viewBox="0 0 100 50"><title>대역</title><text x="1" y="20">관계 미확인</text></svg>',
                    "출처": [model.titles[0]],
                }
            ],
        )
        return {**response, "content": json.dumps(obj, ensure_ascii=False)}

    model = AlteredModel("synthesize", visuals)
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "completed", run
        markdown = service.report_bytes(run["report_id"])[0]["markdown"]
        assert "| 항목 | 값 | 출처 |" in markdown
        assert "A\\|B" in markdown and "날짜 미확인" in markdown
        assert "```svg\n" in markdown and "대역 도식" in markdown
        assert all(body in markdown for body in model.bodies.values())
        assert len([c for c in model.calls if c[2]["operation"] == "synthesize"]) == 1
        assert run["visual_summary"]["도식"][0]["출처"] == [model.titles[0]]
    finally:
        await service.close()


@pytest.mark.parametrize("mutation", ["missing", "unknown_source"])
async def test_신규종합은_누락스키마와_미독시각출처를_거부한다(data_dir, mutation):
    def bad(response):
        obj = json.loads(response["content"])
        if mutation == "missing":
            obj.pop("표")
        else:
            obj["표"] = [
                {
                    "제목": "표",
                    "열": ["가", "나"],
                    "행": [{"셀": ["가", "나"], "출처": ["없는문서"]}],
                }
            ]
        return {**response, "content": json.dumps(obj, ensure_ascii=False)}

    model = AlteredModel("synthesize", bad)
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "failed"
        assert run["report_id"] is None
        errors = [
            e["error"]["message"]
            for e in service.repo.archive(run["run_id"])["events"]
            if e["error"]
        ]
        assert any("synthesize" in message for message in errors)
    finally:
        await service.close()


@pytest.mark.parametrize("decision", ["negative", "contradiction", "missing", "non_json"])
async def test_의미검토_불합격과_불명응답은_초안보존_큐차단_재호출없음(data_dir, decision):
    def reject(response):
        obj = {
            "통과": False,
            "문제": [
                {
                    "유형": "날짜/귀속",
                    "설명": "시험용 오류",
                    "본문발췌": "자료에 근거한 연구 대역 문장",
                    "출처": [model.titles[0]],
                }
            ],
            "검토요약": "명시적 음성 대역",
        }
        if decision == "contradiction":
            obj["통과"] = True
        elif decision == "missing":
            obj.pop("문제")
        return {
            **response,
            "content": "not JSON"
            if decision == "non_json"
            else json.dumps(obj, ensure_ascii=False),
        }

    model = AlteredModel("semantic_review", reject)
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        service.admit("/큐 보존", "queue")
        await settle(service)
        snap = service.repo.snapshot()
        run = snap["runs"][0]
        assert run["status"] == "blocked"
        assert run["phase"] == "quality_failed"
        assert run["draft"] and run["report_id"] is None
        assert snap["queue_paused"] and len(snap["queue"]) == 1
        reason = "semantic_review_failed" if decision == "negative" else "semantic_review_invalid"
        assert reason in run["quality"]["reasons"]
        assert sum(c[2]["operation"] == "semantic_review" for c in model.calls) == 1
        assert not list(data_dir.glob("*.md"))
        assert run["quality"]["semantic_review_raw"]
    finally:
        await service.close()


@pytest.mark.parametrize(
    "bad_content, reason",
    [
        ("not JSON", "invalid JSON"),
        ('{"본문":"본문", "충분":"false", "부족":""}', "exact bool"),
    ],
)
async def test_원고JSON과_bool실패는_최종파일없이_큐정지(data_dir, bad_content, reason):
    model = AlteredModel("draft_section", lambda r: {**r, "content": bad_content})
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        service.admit("/큐 보존 지시", "queue")
        await settle(service)
        snap = service.repo.snapshot()
        run = snap["runs"][0]
        assert run["status"] == "failed"
        assert snap["queue_paused"] and len(snap["queue"]) == 1
        assert run["report_id"] is None and run["draft"] is None
        assert not list(data_dir.glob("*.md"))
        errors = [
            e["error"]["message"]
            for e in service.repo.archive(run["run_id"])["events"]
            if e["error"]
        ]
        assert any(reason in message for message in errors)
        assert not any(c[2]["operation"] == "synthesize" for c in model.calls)
    finally:
        await service.close()


async def test_실패한_실행은_사유를_run에_보존한다(data_dir):
    """2026-11-07 실사용: 화면의 실행/큐는 `failed`만 보이고 사유 문구는 사건 상세를 열어야 읽혔다."""
    model = AlteredModel("draft_section", lambda r: {**r, "content": "not JSON"})
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "failed"
        failed = [e for e in service.repo.archive(run["run_id"])["events"] if e["error"]]
        assert len(failed) == 1
        assert failed[0]["error"]["message"]
        assert run["failure"] == {
            "code": failed[0]["error"]["code"],
            "message": failed[0]["error"]["message"],
            "actor": failed[0]["actor"],
        }
    finally:
        await service.close()


@pytest.mark.parametrize(
    "bad_citation, field", [("없는문서", "unknown_citations"), ("갑오개혁", "unread_citations")]
)
async def test_허위인용_초안보존_출판차단_자동재생성없음(data_dir, bad_citation, field):
    def inject(response):
        obj = json.loads(response["content"])
        obj["본문"] += f" «{bad_citation}»"
        return {**response, "content": json.dumps(obj, ensure_ascii=False)}

    model = AlteredModel("draft_section", inject)
    assert bad_citation not in model.titles[:4]
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        service.admit("/큐 보존 지시", "queue")
        await settle(service)
        snap = service.repo.snapshot()
        run = snap["runs"][0]
        assert run["status"] == "blocked"
        assert snap["queue_paused"] and len(snap["queue"]) == 1
        assert run["draft"] and run["report_id"] is None
        assert run["quality"][field] == [bad_citation]
        assert "citation_integrity_error" in run["quality"]["reasons"]
        assert not list(data_dir.glob("*.md"))
        assert sum(c[2]["operation"] == "synthesize" for c in model.calls) == 1
        assert not [
            e
            for e in service.repo.archive(run["run_id"])["events"]
            if e["type"] == "report.submitted"
        ]
    finally:
        await service.close()


class DelayedModel(ScriptedModel):
    def __init__(self, operation="plan"):
        super().__init__()
        self.operation = operation
        self.arrived, self.release = asyncio.Event(), asyncio.Event()

    async def complete(self, *args, **kwargs):
        response = await super().complete(*args, **kwargs)
        context = kwargs["context"]
        if context["revision"] == 1 and context["operation"] == self.operation:
            self.arrived.set()
            await self.release.wait()
        return response


@pytest.mark.parametrize("operation", ["plan", "read_one", "synthesize", "semantic_review"])
async def test_늦은응답_중에도_새지시즉시적용_초기질문보존_과거출판차단(data_dir, operation):
    model = DelayedModel(operation)
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        admission = service.admit("처음 조사 질문", "first")
        await asyncio.wait_for(model.arrived.wait(), 2)
        service.admit("원인을 강조하라", "second")
        for _ in range(400):
            snap = service.repo.snapshot()
            if snap["runs"][0]["status"] == "completed":
                break
            await asyncio.sleep(0.005)
        run = snap["runs"][0]
        assert run["control_revision"] == 2 and run["status"] == "completed"
        planner_input = next(
            c[1] for c in model.calls if c[2]["revision"] == 2 and c[2]["operation"] == "plan"
        )
        assert "처음 조사 질문" in planner_input and "원인을 강조하라" in planner_input
        stable = run.copy()
        calls = len(model.calls)
        model.release.set()
        await settle(service)
        assert service.repo.snapshot()["runs"][0] == stable
        assert len(model.calls) == calls
        archive = service.repo.archive(admission["run_id"])
        assert [
            e
            for e in archive["events"]
            if e["control_revision"] == 1 and e["type"] == "result.superseded"
        ]
        prohibited = (
            {"publisher"}
            if operation == "semantic_review"
            else {"evaluator", "publisher"}
            if operation == "synthesize"
            else {"editor", "evaluator", "publisher"}
        )
        assert not [
            e for e in archive["events"] if e["control_revision"] == 1 and e["actor"] in prohibited
        ]
    finally:
        await service.close()


async def test_예산차단은_미전송_0회_큐정지_비밀오류_비보존(data_dir):
    class BudgetError(Exception):
        code = "attempt_limit"
        safe_message = "승인된 호출 예산을 초과했습니다."

    class UnsentModel(ScriptedModel):
        async def complete(self, *args, **kwargs):
            raise BudgetError("SECRET-RAW-ERROR")

    model = UnsentModel()
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        service.admit("/큐 보존", "queue")
        await settle(service)
        snap = service.repo.snapshot()
        run = snap["runs"][0]
        assert run["status"] == "failed" and run["actual_model_requests"] == 0
        assert snap["queue_paused"] and len(snap["queue"]) == 1
        assert model.calls == [] and not list(data_dir.glob("*.md"))
        archive = service.repo.archive(run["run_id"])
        assert "SECRET-RAW-ERROR" not in json.dumps(archive)
        assert not [e for e in archive["events"] if e["type"] == "model.started"]
        assert any(
            e["type"] == "model.failed" and e["input"]["sent"] is False for e in archive["events"]
        )
    finally:
        await service.close()


async def test_취소후_늦은모델응답은_다음노드와_출판을_진행못한다(data_dir):
    model = DelayedModel()
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        a = service.admit("질문", "first")
        await asyncio.wait_for(model.arrived.wait(), 2)
        service.cancel(a["run_id"], a["control_revision"], "cancel")
        model.release.set()
        await settle(service)
        snap = service.repo.snapshot()
        assert snap["runs"][0]["status"] == "cancelled"
        assert snap["queue_paused"] and len(model.calls) == 1
        assert snap["runs"][0]["draft"] is None and not list(data_dir.glob("*.md"))
    finally:
        await service.close()


async def test_실제후보외_문서선택은_코퍼스에_있어도_실패한다(data_dir):
    def invalid_selection(response):
        return {**response, "content": '{"문서":"운요호 사건"}'}

    model = AlteredModel("select", invalid_selection)
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "failed" and run["report_id"] is None
        assert not any(c[2]["operation"] == "synthesize" for c in model.calls)
        assert len([c for c in model.calls if c[2]["operation"] == "read_one"]) == 4
        errors = [
            e["error"]["message"]
            for e in service.repo.archive(run["run_id"])["events"]
            if e["error"]
        ]
        assert any("not an unread presented candidate" in message for message in errors)
    finally:
        await service.close()


async def test_ASGI_조립은_동일보고서_열기다운로드_기록_무재호출(data_dir):
    import httpx

    from 서버.앱 import create_app

    model = ScriptedModel()
    app = create_app(
        data_dir=data_dir, service_factory=lambda path: RealResearchService(path, model, CORPUS)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.get("/api/health")).json()["actual_model_requests"] == 0
        assert (await client.get("/api/spike/snapshot")).json()["mode"] == "research"
        assert model.calls == []
        response = await client.post(
            "/api/spike/commands", json={"text": "1894년 사건과 원인", "client_request_id": "first"}
        )
        assert response.status_code == 202
        await settle(app.state.service)
        snap = (await client.get("/api/spike/snapshot")).json()
        run = snap["runs"][0]
        assert run["status"] == "completed"
        report = (await client.get(f"/api/spike/reports/{run['report_id']}")).json()
        download = await client.get(report["download_url"])
        assert download.content == report["markdown"].encode("utf-8")
        count = len(model.calls)
        archive = (await client.get(f"/api/spike/runs/{run['run_id']}/archive")).json()
        started = [e for e in archive["events"] if e["type"] == "model.started"]
        completed = [e for e in archive["events"] if e["type"] == "model.completed"]
        assert len(started) == len(completed) == count
        assert run["actual_model_requests"] == count
        assert all(e["input"]["system"] and e["input"]["user"] for e in started)
        assert all(
            e["output"]["request_id"] == "mock-request"
            and e["output"]["usage"]["output_tokens"] == 13
            for e in completed
        )
        for e in completed:
            details = (await client.get(f"/api/spike/events/{e['event_id']}")).json()
            assert details["output"]["attempt_id"] == e["output"]["attempt_id"]
        again = (await client.get("/api/spike/snapshot")).json()
        assert again["actual_model_requests"] == count and len(model.calls) == count
        assert (
            await client.post("/api/spike/releases", json={"gate_ids": [], "outcome": "success"})
        ).status_code == 409
    assert model.closed


async def test_시도ID_불일치는_결과적용_전에_안전실패(data_dir):
    def wrong_id(response):
        return {**response, "attempt_id": "not-the-sent-attempt"}

    model = AlteredModel("plan", wrong_id)
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("사건 질문", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "failed"
        assert run["plan"] == [] and run["draft"] is None and run["report_id"] is None
        assert run["actual_model_requests"] == 1
        assert len(model.calls) == 1
    finally:
        await service.close()


@pytest.mark.parametrize("action", ["cancel", "delete"])
async def test_의미검토대기중_취소삭제는_정본과_지운기록을_재생성하지_않는다(data_dir, action):
    from 서버.보고서변환 import ReportExports
    from 서버.실행삭제 import RunDeletion

    model = DelayedModel("semantic_review")
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        admission = service.admit("질문", "first")
        run_id = admission["run_id"]
        await asyncio.wait_for(model.arrived.wait(), 3)
        run = service.repo.snapshot()["runs"][0]
        assert run["draft"] and run["report_id"] is None
        count = len(model.calls)
        service.cancel(run_id, 1, "cancel")
        if action == "delete":
            assert (
                RunDeletion(service, ReportExports(service)).delete(run_id, run_id, "delete")[
                    "status"
                ]
                == "deleted"
            )
        model.release.set()
        await settle(service)
        assert len(model.calls) == count
        assert not list(data_dir.glob("*.md"))
        with service.repo.read_connection() as db:
            assert db.execute("SELECT count(*) FROM reports").fetchone()[0] == 0
            if action == "delete":
                assert (
                    db.execute("SELECT count(*) FROM calls WHERE run_id=?", (run_id,)).fetchone()[0]
                    == 0
                )
                assert (
                    db.execute(
                        "SELECT count(*) FROM revisions WHERE run_id=?", (run_id,)
                    ).fetchone()[0]
                    == 0
                )
                assert (
                    db.execute(
                        "SELECT count(*) FROM events WHERE json_extract(body,'$.run_id')=?",
                        (run_id,),
                    ).fetchone()[0]
                    == 0
                )
    finally:
        model.release.set()
        await service.close()


@pytest.mark.parametrize(
    "code,status", [("provider_http", 401), ("provider_http", 429), ("budget_exceeded", None)]
)
async def test_의미검토_제공자오류와_예산차단은_품질통과가_아니다(data_dir, code, status):
    class SafeError(Exception):
        safe_message = "안전한 의미검토 경계 오류"

    class ReviewErrorModel(ScriptedModel):
        async def complete(self, *args, **kwargs):
            if kwargs["context"]["operation"] == "semantic_review":
                error = SafeError("SECRET-RAW-ERROR")
                error.code, error.status = code, status
                if code != "budget_exceeded":
                    kwargs["on_attempt"]("failed-review")
                raise error
            return await super().complete(*args, **kwargs)

    model = ReviewErrorModel()
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        service.admit("/큐 보존", "queue")
        await settle(service)
        snap = service.repo.snapshot()
        run = snap["runs"][0]
        assert run["status"] == "failed" and run["draft"] and run["report_id"] is None
        assert run["quality"]["passed"] is not True
        assert snap["queue_paused"] and len(snap["queue"]) == 1
        assert run["actual_model_requests"] == len(model.calls) + (code != "budget_exceeded")
        archive = service.repo.archive(run["run_id"])
        assert "SECRET-RAW-ERROR" not in json.dumps(archive)
        failed = [e for e in archive["events"] if e["type"] == "model.failed"]
        assert len(failed) == 1 and failed[0]["error"]["code"] == code
        assert not list(data_dir.glob("*.md"))
    finally:
        await service.close()


async def test_같은revision_동시평가는_의미검토를_한번만_예약한다(data_dir):
    model = DelayedModel("semantic_review")
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        admission = service.admit("질문", "first")
        await asyncio.wait_for(model.arrived.wait(), 3)
        run = service.repo.snapshot()["runs"][0]
        sections = [
            e["output"]
            for e in service.repo.archive(admission["run_id"])["events"]
            if e["type"] == "research.completed"
        ]
        await service.evaluate(
            {
                "run_id": admission["run_id"],
                "revision": 1,
                "results": sections,
                "draft": run["draft"],
                "title": run["report_title"],
            }
        )
        assert sum(c[2]["operation"] == "semantic_review" for c in model.calls) == 1
        assert service.repo.snapshot()["runs"][0]["report_id"] is None
        model.release.set()
        await settle(service)
        assert service.repo.snapshot()["runs"][0]["status"] == "completed"
    finally:
        model.release.set()
        await service.close()


async def test_로컬4절2라운드_최대55모델호출_의미검토한번(data_dir):
    class MaximumModel(ExplorationModel):
        async def complete(self, *args, **kwargs):
            response = await super().complete(*args, **kwargs)
            if kwargs["context"]["operation"] == "draft_section":
                actor = kwargs["context"]["actor"]
                sufficient = self.section_calls[actor] == 2
                obj = json.loads(response["content"])
                obj.update(충분=sufficient, 부족="추가 근거 필요" if not sufficient else "")
                response["content"] = json.dumps(obj, ensure_ascii=False)
            return response

    model = MaximumModel()
    service = RealResearchService(data_dir, model, CORPUS)
    try:
        service.admit("질문", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "completed", run
        assert run["actual_model_requests"] == len(model.calls) == 55
        assert model.section_calls == {f"researcher-{i}": 2 for i in range(1, 5)}
        assert sum(c[2]["operation"] == "semantic_review" for c in model.calls) == 1
    finally:
        await service.close()
