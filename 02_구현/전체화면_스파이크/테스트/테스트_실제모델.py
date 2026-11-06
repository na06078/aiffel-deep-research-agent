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
        ("429", "provider_http", 429),
        ("timeout", "timeout", None),
        ("secret", "provider_error", None),
    ],
)
def test_실패는_단한번_안전오류_예약유지(tmp_path, kind, code, status):
    secret = "FAKE_SECRET_NEVER_LOG_abc123"
    sent = []

    def handler(request):
        sent.append(request)
        if kind == "429":
            return httpx.Response(
                429, json={"error": {"message": secret}}, headers={"retry-after": "0"}
            )
        if kind == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        raise RuntimeError(secret)

    model = model_for(tmp_path, handler)

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete("dummy", "dummy", context=CTX)
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
def test_비정상출력은_결과가아니며_확인usage만정산(tmp_path, content, finish, json_mode):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json=response(content, finish))

    model = model_for(tmp_path, handler)

    async def run():
        try:
            with pytest.raises(ModelCallError) as caught:
                await model.complete("dummy JSON", "dummy JSON", context=CTX, json_mode=json_mode)
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
def test_전송전차단과_콜백오류(tmp_path, kind, code, attempts):
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
