import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from 서버.실제모델 import ModelCallError, OpenAIModel

CTX = {"run_id": "dummy-run", "revision": 1, "actor": "dummy", "operation": "probe"}


def response(content='{"ok":true}', finish="stop"):
    return {
        "id": "dummy-completion",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-5.6-luna",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 30,
            "total_tokens": 130,
            "completion_tokens_details": {"reasoning_tokens": 10},
        },
    }


def model_for(tmp_path, handler):
    client = AsyncOpenAI(
        api_key="dummy-not-a-real-key",
        admin_api_key="",
        organization="",
        project="",
        webhook_secret="",
        base_url="https://api.openai.com/v1",
        max_retries=2,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return OpenAIModel(tmp_path / "존재하지않는.env", tmp_path / "예산.sqlite3", client=client)


def test_HTTP1회_예약콜백_요청옵션_정산(tmp_path):
    sent, callbacks = [], []

    def handler(request):
        sent.append(json.loads(request.content))
        assert callbacks and model.usage()["attempt_count"] == 1
        return httpx.Response(200, json=response(), headers={"x-request-id": "req_dummy"})

    model = model_for(tmp_path, handler)

    async def run():
        try:
            return await model.complete(
                "JSON system", "JSON user", context=CTX, json_mode=True, on_attempt=callbacks.append
            )
        finally:
            await model.aclose()

    result = asyncio.run(run())
    assert len(sent) == 1
    assert len(callbacks) == 1
    assert sent[0]["model"] == "gpt-5.6-luna"
    assert sent[0]["reasoning_effort"] == "low"
    assert sent[0]["max_completion_tokens"] == 4096
    assert sent[0]["response_format"] == {"type": "json_object"}
    assert "temperature" not in sent[0]
    assert result["content"] == '{"ok":true}'
    assert result["attempt_id"] == callbacks[0]
    assert result["request_id"] == "req_dummy"
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 30, "reasoning_tokens": 10}
    assert result["estimated_cost_usd"] == pytest.approx(0.000061)
    state = model.usage()
    assert state["reserved_usd"] == 0
    assert state["spent_usd"] == pytest.approx(0.000061)
    assert state["attempt_count"] == 1


@pytest.mark.parametrize(
    "kind,code,status",
    [
        ("402", "provider_http", 402),
        ("429", "provider_http", 429),
        ("timeout", "timeout", None),
        ("secret", "provider_error", None),
    ],
)
@pytest.mark.parametrize("structured", [False, True])
def test_실패는_단한번_안전오류_예약유지(tmp_path, kind, code, status, structured):
    secret = "FAKE_SECRET_NEVER_LOG_abc123"
    sent = []

    def handler(request):
        sent.append(request)
        if kind in ("402", "429"):
            return httpx.Response(
                int(kind), json={"error": {"message": secret}}, headers={"retry-after": "0"}
            )
        if kind == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        raise RuntimeError(secret)

    model = model_for(tmp_path, handler)

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete(
                    "dummy",
                    "dummy",
                    context=CTX,
                    response_schema=STRICT_SCHEMA if structured else None,
                )
            err = caught.value
            assert err.code == code
            assert err.status == status
            assert err.attempt_id
            assert secret not in str(err)
            assert secret not in repr(err.__dict__)
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(sent) == 1
    state = model.usage()
    assert state["unknown_count"] == 1
    assert state["reserved_usd"] > 0
    assert state["spent_usd"] == 0


@pytest.mark.parametrize(
    "content,finish,json_mode",
    [
        ("", "stop", False),
        ('{"value": NaN}', "stop", True),
        ("FAKE_SECRET_bad_json", "stop", True),
        ("[]", "stop", True),
        ('{"ok":true}', "length", True),
        ("partial", "content_filter", False),
    ],
)
@pytest.mark.parametrize("structured", [False, True])
def test_비정상출력은_결과가아니며_확인usage만정산(
    tmp_path, content, finish, json_mode, structured
):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json=response(content, finish))

    model = model_for(tmp_path, handler)

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete(
                    "dummy JSON",
                    "dummy JSON",
                    context=CTX,
                    json_mode=json_mode,
                    response_schema=STRICT_SCHEMA if structured else None,
                )
            assert caught.value.code == "invalid_response"
            assert "FAKE_SECRET" not in str(caught.value)
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(sent) == 1
    assert model.usage()["spent_usd"] == pytest.approx(0.000061)


@pytest.mark.parametrize(
    "kind,code,attempts",
    [
        ("money", "budget_exceeded", 0),
        ("long", "input_too_large", 0),
        ("callback", "attempt_callback_failed", 1),
    ],
)
@pytest.mark.parametrize("structured", [False, True])
def test_전송전차단과_콜백오류(tmp_path, kind, code, attempts, structured):
    sent = []
    model = model_for(tmp_path, lambda req: sent.append(req))

    def broken(_):
        raise RuntimeError("FAKE_SECRET_callback")

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete(
                    "x" * (272001 if kind == "long" else 1),
                    "y",
                    context=CTX,
                    max_output_tokens=1000000 if kind == "money" else 4096,
                    on_attempt=broken if kind == "callback" else None,
                    response_schema=STRICT_SCHEMA if structured else None,
                )
            assert caught.value.code == code
            assert "FAKE_SECRET" not in str(caught.value)
            assert bool(caught.value.attempt_id) == bool(attempts)
        finally:
            await model.aclose()

    asyncio.run(run())
    assert sent == []
    assert model.usage()["attempt_count"] == attempts


def test_프로젝트env만_명시설정_공식주소_실제SDK생성(tmp_path, monkeypatch):
    import 서버.실제모델 as module

    env = tmp_path / "더미.env"
    env.write_text("OTHER=ignored\nOPENAI_API_KEY='dummy-project-only'\n", encoding="utf-8")
    sent = []

    def handler(request):
        sent.append(request)
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer dummy-project-only"
        assert "x-ambient" not in request.headers
        assert not request.headers.get("openai-organization")
        return httpx.Response(200, json=response())

    original = AsyncOpenAI

    def factory(**kw):
        kw["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return original(**kw)

    monkeypatch.setattr(module, "AsyncOpenAI", factory, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-wrong-ambient")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("OPENAI_ORG_ID", "dummy-wrong-org")
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", "Authorization: Bearer dummy-wrong-auth\nX-Ambient: dummy-wrong"
    )
    model = module.OpenAIModel(env, tmp_path / "예산.sqlite3")

    async def run():
        try:
            result = await model.complete("dummy", "dummy", context=CTX)
            assert result["content"]
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(sent) == 1


@pytest.mark.parametrize("kind", ["missing", "negative", "overflow", "bad_reasoning", "bad_model"])
def test_불명또는잘못된usage는_예약을해제하지않음(tmp_path, kind):
    payload = response()
    if kind == "missing":
        del payload["usage"]
    elif kind == "negative":
        payload["usage"]["prompt_tokens"] = -1
    elif kind == "overflow":
        payload["usage"]["completion_tokens"] = 999999
    elif kind == "bad_reasoning":
        payload["usage"]["completion_tokens_details"]["reasoning_tokens"] = 31
    else:
        payload["model"] = "FAKE_SECRET_wrong_model"
    model = model_for(tmp_path, lambda req: httpx.Response(200, json=payload))

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete("dummy", "dummy", context=CTX)
            assert caught.value.code == "invalid_response"
            assert "FAKE_SECRET" not in str(caught.value)
        finally:
            await model.aclose()

    asyncio.run(run())
    assert model.usage()["unknown_count"] == 1
    assert model.usage()["reserved_usd"] > 0


def test_취소후재시작도_예약보존(tmp_path):
    async def handler(request):
        raise asyncio.CancelledError("FAKE_SECRET_cancel")

    model = model_for(tmp_path, handler)

    async def run():
        try:
            with pytest.raises(asyncio.CancelledError) as caught:
                await model.complete("dummy", "dummy", context=CTX)
            assert "FAKE_SECRET" not in str(caught.value)
        finally:
            await model.aclose()

    asyncio.run(run())
    restart = model_for(tmp_path, lambda req: None)
    assert restart.usage()["attempt_count"] == 1
    assert restart.usage()["unknown_count"] == 1
    asyncio.run(restart.aclose())


@pytest.mark.parametrize("kind", ["object", "id", "role"])
def test_응답봉투도_정식스키마를_검증(tmp_path, kind):
    payload = response()
    if kind == "object":
        payload["object"] = "FAKE_SECRET_wrong_object"
    elif kind == "id":
        payload["id"] = None
    else:
        payload["choices"][0]["message"]["role"] = "user"
    model = model_for(tmp_path, lambda req: httpx.Response(200, json=payload))

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete("dummy", "dummy", context=CTX)
            assert caught.value.code == "invalid_response"
            assert "FAKE_SECRET" not in str(caught.value)
        finally:
            await model.aclose()

    asyncio.run(run())
    assert model.usage()["unknown_count"] == 1


def test_40번째전송후_41번째는_콜백도HTTP도없음(tmp_path):
    sent, callbacks = [], []

    def handler(request):
        sent.append(request)
        return httpx.Response(429, json={"error": {"message": "dummy-limit"}})

    model = model_for(tmp_path, handler)

    async def run():
        try:
            for _ in range(40):
                with pytest.raises(ModelCallError) as caught:
                    await model.complete("dummy", "dummy", context=CTX, on_attempt=callbacks.append)
                assert caught.value.code == "provider_http"
            with pytest.raises(ModelCallError) as caught:
                await model.complete("dummy", "dummy", context=CTX, on_attempt=callbacks.append)
            assert caught.value.code == "budget_exceeded"
            assert caught.value.attempt_id is None
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(sent) == len(callbacks) == 40
    assert model.usage()["attempt_count"] == 40


def test_초기화한100회상한은_101번째콜백과전송을차단(tmp_path):
    sent, callbacks = [], []

    def handler(request):
        sent.append(request)
        return httpx.Response(429, json={"error": {"message": "dummy-limit"}})

    model = model_for(tmp_path, handler)
    model.budget.reset_attempt_counter(max_attempts=100, max_usd=2)

    async def run():
        try:
            for _ in range(100):
                with pytest.raises(ModelCallError) as caught:
                    await model.complete("dummy", "dummy", context=CTX, on_attempt=callbacks.append)
                assert caught.value.code == "provider_http"
            with pytest.raises(ModelCallError) as caught:
                await model.complete("dummy", "dummy", context=CTX, on_attempt=callbacks.append)
            assert caught.value.code == "budget_exceeded"
            assert caught.value.attempt_id is None
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(sent) == len(callbacks) == 100
    assert model.usage()["attempt_count"] == 100
    assert model.usage()["remaining_attempts"] == 0


@pytest.mark.parametrize(
    "text",
    [
        "OTHER=dummy",
        "OPENAI_API_KEY=\n",
        "OPENAI_API_KEY=${OTHER}",
        "OPENAI_API_KEY='dummy\n",
        "OPENAI_API_KEY=dummy1\nOPENAI_API_KEY=dummy2",
    ],
)
def test_env설정오류는_ambient키로_우회하지않음(tmp_path, monkeypatch, text):
    env = tmp_path / "더미오류.env"
    env.write_text(text, encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-ambient-not-allowed")
    with pytest.raises(ModelCallError) as caught:
        OpenAIModel(env, tmp_path / "예산.sqlite3")
    assert caught.value.code == "configuration_error"
    assert caught.value.attempt_id is None
    assert "dummy" not in str(caught.value)


STRICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"근거구간": {"type": "array", "items": {"type": "integer", "enum": [1, 3]}}},
    "required": ["근거구간"],
}


def test_strict_schema_실제SDK_payload와예약UTF8사본(tmp_path):
    import copy

    sent, reserved = [], []
    schema = copy.deepcopy(STRICT_SCHEMA)
    expected = copy.deepcopy(schema)
    model = model_for(
        tmp_path,
        lambda req: sent.append(json.loads(req.content)) or httpx.Response(200, json=response()),
    )
    original = model.budget.reserve

    def reserve(bound, output, context):
        reserved.append(bound)
        schema["properties"]["근거구간"]["items"]["enum"].append(99)
        return original(bound, output, context)

    model.budget.reserve = reserve

    async def run():
        try:
            await model.complete(
                "시스템", "사용자", context=CTX, json_mode=True, response_schema=schema
            )
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(sent) == 1
    assert sent[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "grounded_section", "strict": True, "schema": expected},
    }
    assert reserved == [
        len("시스템".encode())
        + len("사용자".encode())
        + len(json.dumps(expected, ensure_ascii=False).encode())
        + 8192
    ]


@pytest.mark.parametrize(
    "schema,code",
    [
        ([], "invalid_request"),
        ({"type": "array"}, "invalid_request"),
        (
            {
                "type": "json_schema",
                "json_schema": {"name": "bad", "strict": True, "schema": STRICT_SCHEMA},
            },
            "invalid_request",
        ),
        ({"type": "object", "items": []}, "invalid_request"),
        ({"type": "object", "properties": {"x": {"enum": []}}}, "invalid_request"),
        ({"type": "object", "properties": {1: {"type": "string"}}}, "invalid_request"),
        ({"type": "object", "required": [True]}, "invalid_request"),
        (
            {"type": "object", "properties": {"x": {"type": "string", "maxLength": True}}},
            "invalid_request",
        ),
        ({"type": "object", "properties": []}, "invalid_request"),
        ({"type": "object", "properties": {"x": {"type": "nonsense"}}}, "invalid_request"),
        ({"type": "object", "description": float("nan")}, "invalid_request"),
        ({"type": "object", "description": (1, 2)}, "invalid_request"),
        ({**STRICT_SCHEMA, "description": "가" * 90000}, "input_too_large"),
    ],
)
def test_schema거절은_예약콜백SDK전(tmp_path, schema, code):
    sent, reserved, callbacks = [], [], []
    model = model_for(tmp_path, lambda req: sent.append(req))
    model.budget.reserve = lambda *args: reserved.append(args)

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete(
                    "JSON",
                    "JSON",
                    context=CTX,
                    json_mode=True,
                    response_schema=schema,
                    on_attempt=callbacks.append,
                )
            assert caught.value.code == code
            assert caught.value.attempt_id is None
        finally:
            await model.aclose()

    asyncio.run(run())
    assert sent == reserved == callbacks == []
    assert model.usage()["attempt_count"] == 0


@pytest.mark.parametrize("location", ["root", "nested"])
@pytest.mark.parametrize(
    "defect", ["missing_required", "partial_required", "missing_closed", "open"]
)
def test_NUM_SCHEMA_01_모든object는_닫힌필수필드이며_예약전거부(tmp_path, location, defect):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["rows"],
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["x"],
                    "properties": {"x": {"type": "string"}},
                },
            }
        },
    }
    target = schema if location == "root" else schema["properties"]["rows"]["items"]
    if defect == "missing_required":
        target.pop("required")
    elif defect == "partial_required":
        target["required"] = []
    elif defect == "missing_closed":
        target.pop("additionalProperties")
    else:
        target["additionalProperties"] = True
    sent, callbacks = [], []
    model = model_for(
        tmp_path, lambda request: sent.append(request) or httpx.Response(200, json=response())
    )

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete(
                    "JSON", "JSON", context=CTX, response_schema=schema, on_attempt=callbacks.append
                )
            assert caught.value.code == "invalid_request" and caught.value.attempt_id is None
        finally:
            await model.aclose()

    asyncio.run(run())
    assert sent == callbacks == [] and model.usage()["attempt_count"] == 0


def test_실제builder_schema의_정수enum이_SDK_HTTP까지_동일하다(tmp_path):
    from 서버.보고서근거 import build_draft_request

    request = build_draft_request(
        {"title": "시기", "role": "시간순 담당", "instruction": "원문 확인"},
        [["자료", "읽은 메모"]],
        {"자료": "1894년에 사건이 일어났다. " * 25},
    )
    catalog = json.loads(request.prompts[1])
    schema = request.response_schema
    sent = []
    content = json.dumps(
        {
            "근거목록ID": catalog["evidence_set_id"],
            "문장": [
                {
                    "내용": "자료에 기록된 사건을 정리한다.",
                    "근거구간": [catalog["evidence"][0]["id"]],
                }
            ],
            "충분": True,
            "부족": "",
        },
        ensure_ascii=False,
    )
    model = model_for(
        tmp_path,
        lambda req: (
            sent.append(json.loads(req.content)) or httpx.Response(200, json=response(content))
        ),
    )

    async def run():
        try:
            result = await model.complete(
                *request.prompts, context=CTX, json_mode=True, response_schema=schema
            )
            assert result["content"] == content
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(sent) == 1
    actual = sent[0]["response_format"]
    assert actual == {
        "type": "json_schema",
        "json_schema": {"name": "grounded_section", "strict": True, "schema": schema},
    }
    assert actual["json_schema"]["schema"]["properties"]["문장"]["items"]["properties"]["근거구간"][
        "items"
    ]["enum"] == [e["id"] for e in catalog["evidence"]]
    assert all(type(e["id"]) is int for e in catalog["evidence"])


@pytest.mark.parametrize("kind", ["refusal", "tool_calls"])
def test_strict_schema도_거부와도구호출은_출판응답아님(tmp_path, kind):
    payload = response()
    message = payload["choices"][0]["message"]
    if kind == "refusal":
        message["refusal"] = "dummy refusal"
    else:
        message["tool_calls"] = [
            {"id": "dummy", "type": "function", "function": {"name": "dummy", "arguments": "{}"}}
        ]
    sent = []
    model = model_for(tmp_path, lambda req: sent.append(req) or httpx.Response(200, json=payload))

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete(
                    "JSON", "JSON", context=CTX, json_mode=True, response_schema=STRICT_SCHEMA
                )
            assert caught.value.code == "invalid_response"
        finally:
            await model.aclose()

    asyncio.run(run())
    assert len(sent) == 1 and model.usage()["unknown_count"] == 0
    assert model.usage()["spent_usd"] == pytest.approx(0.000061)


def test_schema실패두건_재시작과정상정산도_과거예약불변(tmp_path):
    model = model_for(
        tmp_path, lambda req: httpx.Response(429, json={"error": {"message": "dummy"}})
    )

    async def fail_twice():
        try:
            for _ in range(2):
                with pytest.raises(ModelCallError, match="모델 제공자"):
                    await model.complete("JSON", "JSON", context=CTX, response_schema=STRICT_SCHEMA)
        finally:
            await model.aclose()

    asyncio.run(fail_twice())
    old = model.usage()
    restart = model_for(tmp_path, lambda req: httpx.Response(200, json=response()))

    async def success():
        try:
            await restart.complete("JSON", "JSON", context=CTX, response_schema=STRICT_SCHEMA)
        finally:
            await restart.aclose()

    asyncio.run(success())
    usage = restart.usage()
    assert old["unknown_count"] == usage["unknown_count"] == 2
    assert old["reserved_usd"] == usage["reserved_usd"] > 0
    assert old["attempt_count"] == 2 and usage["attempt_count"] == 3
