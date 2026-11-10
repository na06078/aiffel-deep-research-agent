"""§29.12 정상 서비스 경계. 합성 자료/명시 대역만 사용."""

import json

import pytest

from 서버.검토문맥 import build_review_request, parse_review

# pytest가 같은 격리/무네트워크 fixture를 수집하도록 명시적으로 재노출한다.
from 테스트.테스트_보고서보완연결 import forbid_network as forbid_network  # noqa: PLC0414 - pytest fixture 재노출
from 테스트.테스트_보고서보완연결 import post, settle
from 테스트.테스트_보고서보완연결 import setup_app as setup_app  # noqa: PLC0414 - pytest fixture 재노출


@pytest.mark.parametrize(
    "reason",
    ["contradiction", "citation_gap", "unsupported", "source_conflict", "insufficient_context"],
)
async def test_새계약은_모순만_주장보완_허용(setup_app, reason):
    client, service, model, old, sections = setup_app
    qualified = {s["section_id"]: {"자료": service.corpus.docs["자료"]} for s in sections}
    request = build_review_request(old["draft"], sections, qualified)
    contexts = request.inputs["contexts"]
    refs = [contexts[0]["id"]]
    if reason == "citation_gap":
        # 같은 정확 원문이 인용 전체인 경우 추가 문맥은 없으므로 별도 인용 외 구간을 합성한다.
        service.corpus.docs["자료"] += "\n\n추가 문단은 두 사건의 선후관계를 확정하지 않는다."
        qualified = {s["section_id"]: {"자료": service.corpus.docs["자료"]} for s in sections}
        request = build_review_request(old["draft"], sections, qualified)
        refs = [next(c["id"] for c in request.inputs["contexts"] if c["kind"] != "quote")]
    elif reason == "source_conflict":
        refs = [contexts[0]["id"], contexts[1]["id"]]
    elif reason in ("unsupported", "insufficient_context"):
        refs = []
    raw = json.dumps(
        {
            "검토목록ID": request.inputs["review_set_id"],
            "판정": "needs_revision"
            if reason in ("contradiction", "citation_gap", "unsupported")
            else "undetermined",
            "문제": [
                {
                    "유형": reason,
                    "설명": "명시적 반례",
                    "본문발췌": sections[0]["근거"][0]["주장"],
                    "근거번호": refs,
                }
            ],
            "검토요약": "대역",
        },
        ensure_ascii=False,
    )
    decision = parse_review(raw, request, qualified)
    with service.repo.transaction() as (db, data):
        run = data["runs"][0]
        run["quality"]["semantic_review"] = decision
        run["semantic_review_state"]["raw"] = raw
        service.repo.save_revision(db, run)
    response = await post(client, old)
    if reason == "contradiction":
        assert response.status_code == 202, response.text
        await settle(service)
        assert len(model.calls) == 2
    else:
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "correction_ineligible", response.text
        assert model.calls == []
        assert service.repo.snapshot()["runs"][0]["control_revision"] == 1
        assert "correction_state" not in service.repo.snapshot()["runs"][0]


@pytest.mark.parametrize("mode", ["local", "web"])
async def test_의미응답후_같은절_읽기자격과원문을_재확인(mode, tmp_path):
    import asyncio

    from 서버.실제연구제어 import RealResearchService
    from 테스트.테스트_실제연구제어 import CORPUS, ScriptedModel
    from 테스트.테스트_웹연구 import FakeWebClient, FakeWebStore, WebModel

    base = ScriptedModel if mode == "local" else WebModel

    class Hold(base):
        def __init__(self):
            super().__init__()
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def complete(self, *args, **kwargs):
            response = await super().complete(*args, **kwargs)
            if kwargs["context"]["operation"] == "semantic_review":
                self.entered.set()
                await self.release.wait()
            return response

    model = Hold()
    store = FakeWebStore()
    service = RealResearchService(
        tmp_path / "저널",
        model,
        CORPUS,
        web_client=FakeWebClient() if mode == "web" else None,
        web_store=store,
    )
    try:
        service.admit("/웹 자료" if mode == "web" else "질문", "review-source-fence")
        await asyncio.wait_for(model.entered.wait(), 10)
        assert sum(c[2]["operation"] == "semantic_review" for c in model.calls) == 1
        if mode == "local":
            for key in list(service.corpus.docs):
                service.corpus.docs[key] += "\n원문 버전 변경"
        else:
            store.claims.clear()
        model.release.set()
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "blocked" and run["report_id"] is None
        assert run["quality"]["reasons"] == ["semantic_review_invalid"]
        assert run["quality"]["semantic_review_raw"]
        assert not list((tmp_path / "저널").glob("*.md"))
    finally:
        model.release.set()
        await service.close()


async def test_문맥미제공_범위를_최종품질경고에_보존():
    from 테스트.테스트_웹연구 import make

    service = make()
    try:
        service.admit("/웹 문맥 범위", "scope-warning")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "completed", run
        scope = run["quality"]["semantic_review"]["제공범위"]
        assert scope["incomplete"] is True and scope["omitted"] > 0
        assert any(
            "전체 원문 문맥을 제공하지 않았습니다" in warning
            for warning in run["quality"]["warnings"]
        )
    finally:
        await service.close()


async def test_검토스키마는_실제SDK_payload에_도착하고_장부는_쓰지않는다(monkeypatch):
    from pathlib import Path

    import httpx
    from openai import AsyncOpenAI

    from 서버.실제모델 import OpenAIModel
    from 테스트.테스트_검토문맥 import DRAFT, SECTIONS, SOURCES, payload

    request = build_review_request(DRAFT, SECTIONS, SOURCES)
    sent, reserved, settled, attempts = [], [], [], []

    class MemoryBudget:
        def reserve(self, bound, maximum, context):
            reserved.append((bound, maximum, context))
            return "memory-only-attempt"

        def settle(self, attempt, input_tokens, output_tokens):
            settled.append((attempt, input_tokens, output_tokens))
            return 0.0

    def transport(http_request):
        sent.append(json.loads(http_request.content))
        return httpx.Response(
            200,
            json={
                "id": "explicit-sdk-double",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-5.6-luna",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": payload(request)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    def no_files(*args, **kwargs):
        raise AssertionError("credential_or_ledger_io_forbidden")

    monkeypatch.setattr(Path, "open", no_files)
    # 생성자는 실제 장부를 열므로 생략한다. 실제 complete/SDK와 메모리 예산 대역만 결합한다.
    model = OpenAIModel.__new__(OpenAIModel)
    model.budget = MemoryBudget()
    model.client = AsyncOpenAI(
        api_key="explicit-test-no-credential",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    try:
        response = await model.complete(
            *request.prompts,
            context={"operation": "semantic_review"},
            json_mode=True,
            response_schema=request.response_schema,
            on_attempt=attempts.append,
        )
        assert (
            attempts == ["memory-only-attempt"] and len(sent) == len(reserved) == len(settled) == 1
        )
        assert sent[0]["response_format"]["json_schema"]["strict"] is True
        assert sent[0]["response_format"]["json_schema"]["schema"] == request.response_schema
        assert sent[0]["messages"][1]["content"] == request.prompts[1]
        expected = (
            sum(len(p.encode()) for p in request.prompts)
            + len(json.dumps(request.response_schema, ensure_ascii=False, allow_nan=False).encode())
            + 8192
        )
        assert reserved[0][0] == expected <= 272000
        assert parse_review(response["content"], request, SOURCES)["통과"] is True
    finally:
        await model.aclose()
