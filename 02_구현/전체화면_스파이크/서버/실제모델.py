"""명시적 실제 모델 인터페이스."""

import json
import re
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from .호출예산 import BudgetError, CallBudget


class ModelCallError(Exception):
    def __init__(self, code, safe_message, attempt_id=None, status=None):
        self.code, self.safe_message = code, safe_message
        self.attempt_id, self.status = attempt_id, status
        super().__init__(safe_message)


def _project_key(path):
    """단일 행 KEY=value만 지원. 환경 보간/실행/전역 환경 변경 없음."""
    key = None
    failed = False
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*?)\s*$", line)
            if not match:
                continue
            if key is not None:
                failed = True
                break
            value = match.group(1)
            if value.startswith(("'", '"')):
                quote = value[0]
                end = value.find(quote, 1)
                if end < 1 or (
                    value[end + 1 :].strip() and not value[end + 1 :].strip().startswith("#")
                ):
                    failed = True
                    break
                value = value[1:end]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            if not value or any(c.isspace() for c in value) or "$" in value:
                failed = True
                break
            key = value
    except Exception:  # noqa: BLE001 - 외부 원문/비밀을 안전 오류로 치환하는 경계
        failed = True
    if failed or not key:
        raise ModelCallError(
            "configuration_error", "프로젝트 .env의 OPENAI_API_KEY 설정을 확인하십시오."
        )
    return key


def _response_schema(schema):
    """허용된 plain JSON Schema를 검증하고 전송/크기 계산용 사본을 만든다."""

    def plain(value):
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise ValueError("schema keys")
            for child in value.values():
                plain(child)
        elif type(value) is list:
            for child in value:
                plain(child)
        elif type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("plain JSON required")

    def node(value):
        if type(value) is not dict:
            raise ValueError("schema object required")
        kinds = {"object", "array", "string", "integer", "number", "boolean", "null"}
        kind = value.get("type")
        if kind is not None and not (
            type(kind) is str
            and kind in kinds
            or type(kind) is list
            and kind
            and all(type(k) is str and k in kinds for k in kind)
        ):
            raise ValueError("schema type")
        # strict 요청은 모든 object를 닫고 모든 속성을 필수로 지정해야 한다.
        if kind == "object" or (type(kind) is list and "object" in kind) or "properties" in value:
            properties, required = value.get("properties"), value.get("required")
            if (
                type(properties) is not dict
                or value.get("additionalProperties") is not False
                or type(required) is not list
                or any(type(key) is not str for key in required)
                or len(required) != len(properties)
                or set(required) != set(properties)
            ):
                raise ValueError("strict object fields")
        for key in ("properties", "$defs", "definitions"):
            if key in value:
                if type(value[key]) is not dict:
                    raise ValueError("schema properties")
                for child in value[key].values():
                    node(child)
        if "items" in value:
            node(value["items"])
        if "additionalProperties" in value and type(value["additionalProperties"]) is not bool:
            node(value["additionalProperties"])
        for key in ("anyOf", "allOf", "oneOf"):
            if key in value:
                if type(value[key]) is not list or not value[key]:
                    raise ValueError("schema union")
                for child in value[key]:
                    node(child)
        if "required" in value and (
            type(value["required"]) is not list
            or any(type(k) is not str for k in value["required"])
            or len(set(value["required"])) != len(value["required"])
            or not set(value["required"]) <= set(value.get("properties", {}))
        ):
            raise ValueError("schema required")
        if "enum" in value and (type(value["enum"]) is not list or not value["enum"]):
            raise ValueError("schema enum")
        for key in ("description", "title", "$ref", "$schema", "pattern", "format"):
            if key in value and type(value[key]) is not str:
                raise ValueError("schema string")
        for key in (
            "minItems",
            "maxItems",
            "minLength",
            "maxLength",
            "minProperties",
            "maxProperties",
        ):
            if key in value and (type(value[key]) is not int or value[key] < 0):
                raise ValueError("schema limit")

    invalid = False
    try:
        plain(schema)
        if type(schema) is not dict or schema.get("type") != "object":
            raise ValueError("root object required")
        node(schema)
        encoded = json.dumps(schema, ensure_ascii=False, allow_nan=False)
        detached = json.loads(encoded)
        size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, RecursionError, OverflowError):
        invalid = True
    if invalid:
        raise ModelCallError("invalid_request", "응답 스키마 형식이 올바르지 않습니다.")
    return detached, size


class OpenAIModel:
    def __init__(self, env_path: Path, ledger_path: Path, *, client=None):
        self.budget = CallBudget(ledger_path)
        if client is None:
            key = _project_key(env_path)
            failed = False
            try:
                client = AsyncOpenAI(
                    api_key=key,
                    admin_api_key="",
                    organization="",
                    project="",
                    webhook_secret="",
                    base_url="https://api.openai.com/v1",
                    max_retries=0,
                    timeout=60,
                    http_client=httpx.AsyncClient(
                        trust_env=False, follow_redirects=False, timeout=60
                    ),
                )
                # SDK 3.46은 OPENAI_CUSTOM_HEADERS를 별도로 합친다.
                # 명시적 프로젝트 인증 외 ambient 헤더를 전달하지 않는다.
                client._custom_headers = {}
            except Exception:  # noqa: BLE001 - 외부 원문/비밀을 안전 오류로 치환하는 경계
                failed = True
            if failed:
                raise ModelCallError(
                    "configuration_error", "모델 클라이언트를 초기화하지 못했습니다."
                )
        self.client = client

    async def complete(
        self,
        system: str,
        user: str,
        *,
        context: dict,
        max_output_tokens=4096,
        json_mode=False,
        response_schema=None,
        on_attempt=None,
    ) -> dict:
        if (
            not isinstance(system, str)
            or not isinstance(user, str)
            or type(max_output_tokens) is not int
            or max_output_tokens <= 0
        ):
            raise ModelCallError("invalid_request", "모델 입력 형식이 올바르지 않습니다.")
        schema_bytes = 0
        if response_schema is not None:
            response_schema, schema_bytes = _response_schema(response_schema)
        input_bound = len(system.encode("utf-8")) + len(user.encode("utf-8")) + schema_bytes + 8192
        if input_bound > 272000:
            raise ModelCallError("input_too_large", "긴 컨텍스트 요금 경계의 요청을 차단했습니다.")
        failure = None
        try:
            attempt_id = self.budget.reserve(input_bound, max_output_tokens, context)
        except BudgetError:
            failure = ModelCallError(
                "budget_exceeded", "모델 호출 또는 추정 비용 예산이 소진되었습니다."
            )
        except Exception:  # noqa: BLE001 - 외부 원문/비밀을 안전 오류로 치환하는 경계
            failure = ModelCallError("ledger_error", "호출 예산 장부를 예약하지 못했습니다.")
        if failure:
            raise failure
        if on_attempt:
            try:
                on_attempt(attempt_id)
            except Exception:  # noqa: BLE001 - 외부 원문/비밀을 안전 오류로 치환하는 경계
                failure = ModelCallError(
                    "attempt_callback_failed", "전송 전 시도 기록에 실패했습니다.", attempt_id
                )
            if failure:
                raise failure
        options = {
            "model": "gpt-5.6-luna",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "reasoning_effort": "low",
            "max_completion_tokens": max_output_tokens,
        }
        if response_schema is not None:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "grounded_section",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        elif json_mode:
            options["response_format"] = {"type": "json_object"}
        import asyncio

        from openai import APIStatusError, APITimeoutError

        failure = None
        try:
            call_client = self.client.with_options(
                max_retries=0, timeout=60, base_url="https://api.openai.com/v1"
            )
            call_client._custom_headers = {}
            result = await call_client.chat.completions.create(**options)
        except asyncio.CancelledError:
            failure = asyncio.CancelledError()
        except APITimeoutError:
            failure = ModelCallError("timeout", "모델 요청 시간이 초과되었습니다.", attempt_id)
        except APIStatusError as exc:
            failure = ModelCallError(
                "provider_http", "모델 제공자가 요청을 거절했습니다.", attempt_id, exc.status_code
            )
        except Exception:  # noqa: BLE001 - 외부 원문/비밀을 안전 오류로 치환하는 경계
            failure = ModelCallError("provider_error", "모델 통신에 실패했습니다.", attempt_id)
        if failure is not None:
            raise failure
        invalid_usage = False
        try:
            from openai.types.chat import ChatCompletion

            # SDK 기본 construct는 HTTP 200의 타입/필수필드를 엄격히 검사하지 않는다.
            # 재검증 오류 원문에는 응답이 들어가므로 밖으로 전달하지 않는다.
            ChatCompletion.model_validate(result.model_dump(warnings=False), strict=True)
            usage = result.usage
            reasoning = getattr(usage.completion_tokens_details, "reasoning_tokens", None)
            if reasoning is None:
                reasoning = 0
            nums = (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, reasoning)
            invalid_usage = (
                any(type(n) is not int or n < 0 for n in nums)
                or usage.prompt_tokens > input_bound
                or usage.completion_tokens > max_output_tokens
                or reasoning > usage.completion_tokens
                or usage.total_tokens != usage.prompt_tokens + usage.completion_tokens
                or result.model != "gpt-5.6-luna"
            )
        except Exception:  # noqa: BLE001 - 외부 원문/비밀을 안전 오류로 치환하는 경계
            invalid_usage = True
        if invalid_usage:
            raise ModelCallError(
                "invalid_response", "모델 응답의 사용량을 검증하지 못했습니다.", attempt_id
            )
        try:
            cost = self.budget.settle(attempt_id, usage.prompt_tokens, usage.completion_tokens)
        except Exception:  # noqa: BLE001 - 외부 원문/비밀을 안전 오류로 치환하는 경계
            failure = ModelCallError("ledger_error", "모델 사용량 정산에 실패했습니다.", attempt_id)
        if failure:
            raise failure
        invalid = False
        try:
            choice = result.choices[0]
            content = choice.message.content
            invalid = (
                len(result.choices) != 1
                or choice.finish_reason != "stop"
                or not isinstance(content, str)
                or not content.strip()
                or bool(choice.message.refusal)
                or bool(choice.message.tool_calls)
            )
            if json_mode and not invalid:

                def reject_constant(_):
                    raise ValueError("invalid_json_constant")

                invalid = not isinstance(json.loads(content, parse_constant=reject_constant), dict)
        except Exception:  # noqa: BLE001 - 외부 원문/비밀을 안전 오류로 치환하는 경계
            invalid = True
        if invalid:
            raise ModelCallError(
                "invalid_response", "모델 응답 형식이 올바르지 않거나 잘렸습니다.", attempt_id
            )
        return {
            "content": content,
            "model": result.model,
            "attempt_id": attempt_id,
            "request_id": getattr(result, "_request_id", None),
            "usage": {
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "reasoning_tokens": reasoning,
            },
            "estimated_cost_usd": cost,
        }

    def usage(self):
        return self.budget.usage()

    async def aclose(self):
        if self.client is not None:
            await self.client.close()
