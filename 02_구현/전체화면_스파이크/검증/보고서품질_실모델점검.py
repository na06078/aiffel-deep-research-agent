"""보고서 품질 preflight: 기본/--prepare는 인증·장부·제공자에 접근하지 않는다.

--allow-live --ledger <기존 공유 장부>만 실제 전송을 허용한다. 자동 재시도,
repair/fallback/예산 초기화가 없다. 모델 대역 PASS는 Luna의 검출력 증명이
아니며 이 검사는 6노드/2,000자 전체 앱 수용 검사가 아니다.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
WORK = ROOT / ".cache/보고서품질_검증/실모델점검"
CORPUS_PATH = ROOT / "01_참고자료/원본데이터/corpus.json"
MODEL = "gpt-5.6-luna"
MAX_CALLS = 4
MAX_OUTPUT = 4096
MAX_INPUT_PROXY = 272000
MAX_COST_NANO_USD = MAX_CALLS * (MAX_INPUT_PROXY * 250 + MAX_OUTPUT * 1200)
sys.path.insert(0, str(SPIKE))

from 서버.교안연구 import Corpus
from 서버.종합계약 import SYNTHESIS_PROTOCOL, build_synthesis_schema


def digest(data):
    return hashlib.sha256(data).hexdigest()


def cache_path(work):
    path = Path(work).resolve()
    if not path.is_relative_to(WORK.resolve()):
        raise ValueError("output_outside_preflight_cache")
    return path


def save(work, name, value):
    path = cache_path(work) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def inputs():
    corpus = Corpus(CORPUS_PATH)
    source = corpus.docs["임오군란"]
    quote = source.split(". ", 1)[0] + "."
    if not 10 <= len(quote) <= 400 or "1882년 7월 23일" not in quote or quote not in source:
        raise ValueError("canonical_date_source_changed")
    assignment = {
        "section_id": "section-1",
        "title": "임오군란의 발생 시기",
        "role": "시간순 담당",
        "instruction": "제공된 원문의 날짜·달력을 그대로 보존하고 근거 없는 추가 사실은 쓰지 마라. 임오군란의 1882년 발생 시기를 본문과 근거에 명시하라.",
    }
    notes = [["임오군란", "사전점검용 원문 발췌 메모(LLM 읽기 결과가 아님):\n" + source[:1600]]]
    return corpus, {"assignment": assignment, "notes": notes, "quote": quote}


CONTROL_PROTOCOL = "canonical-source-date-v1"


def control_cases(corpus):
    """생성 결과와 무관하게 현재 실제 원문의 첫 문장으로만 대조를 만든다."""
    source = corpus.docs["임오군란"]
    quote = source.split(". ", 1)[0] + "."
    if (
        not source.startswith(quote)
        or "1882년 7월 23일" not in quote
        or not 10 <= len(quote) <= 400
    ):
        raise ValueError("canonical_date_source_changed")
    negative_claim = quote.replace("1882년", "1883년", 1)
    provenance = {
        "source_id": "임오군란",
        "source_sha256": digest(source.encode("utf-8")),
        "start": 0,
        "end": len(quote),
        "quote": quote,
        "positive_claim": quote,
        "negative_claim": negative_claim,
    }
    cases = {}
    for name, claim in (("positive", quote), ("negative", negative_claim)):
        section = {
            "section_id": "control-1",
            "본문": claim + " «임오군란»",
            "충분": True,
            "부족": "",
            "읽은문서": ["임오군란"],
            "근거": [{"주장": claim, "출처": "임오군란", "인용문": quote}],
        }
        cases[name] = {"draft": "# 임오군란 날짜 대조\n\n" + section["본문"], "sections": [section]}
    return provenance, cases, {"control-1": {"임오군란": source}}


def manifest():
    paths = [
        CORPUS_PATH,
        ROOT / "📘 딥리서치 에이전트 기술 명세서.md",
        Path(__file__),
        SPIKE / "검증/보고서품질_실제연구검사.py",
        SPIKE / "테스트/테스트_보고서품질_실모델점검.py",
        SPIKE / "검증/웹연구_초안프롬프트_실모델점검.py",
    ] + [
        SPIKE / "서버" / name
        for name in (
            "교안연구.py",
            "종합계약.py",
            "보고서품질.py",
            "보고서근거.py",
            "보고서보완.py",
            "근거보완.py",
            "원문구간.py",
            "검토문맥.py",
            "보고서도식.py",
            "보고서표현.py",
            "저장소.py",
            "실제모델.py",
            "호출예산.py",
            "실제연구제어.py",
            "연구앱.py",
            "실험설정.py",
        )
    ]
    return {str(p.resolve()): digest(p.read_bytes()) for p in paths}


def prepare(*, work=WORK):
    from 서버.보고서근거 import build_draft_request

    work = cache_path(work)
    corpus, data = inputs()
    request = build_draft_request(
        data["assignment"], data["notes"], {"임오군란": corpus.docs["임오군란"]}
    )
    system, user = request.prompts
    save(
        work,
        "입력준비.json",
        {
            **data,
            "system": system,
            "user": user,
            "request_inputs": request.inputs,
            "response_schema": request.response_schema,
        },
    )
    result = {
        "mode": "prepare",
        "draft_protocol": request.inputs["draft_protocol"],
        "model": MODEL,
        "live_verified": False,
        "observed_attempts": 0,
        "max_calls": MAX_CALLS,
        "max_output_tokens": MAX_OUTPUT,
        "max_input_proxy_per_call": MAX_INPUT_PROXY,
        "max_retries": 0,
        "max_cost_nano_usd": MAX_COST_NANO_USD,
        "max_cost_usd": MAX_COST_NANO_USD / 1_000_000_000,
        "source_key": "임오군란",
        "source_text_sha256": digest(corpus.docs["임오군란"].encode()),
        "source_hashes": manifest(),
        "cache": str(work),
        "boundary": "실제 Luna 미검증; 대역은 전달/스키마/조립만 검증. 6노드·2000자 앱 수용 검사 아님.",
    }
    save(work, "준비메타데이터.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true", help="인증·장부·외부 호출 없이 준비")
    modes.add_argument(
        "--allow-live", action="store_true", help="기존 공유 장부로 최대4회 실제 전송 허용"
    )
    parser.add_argument("--ledger", type=Path, help="실제 전송 시 기존 공유 장부 절대 경로 필수")
    args = parser.parse_args(argv)
    if not args.allow_live:
        if args.ledger is not None:
            parser.error("--ledger는 --allow-live와 함께만 사용하십시오")
        result = prepare(work=WORK / "prepare")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.ledger is None or not args.ledger.is_absolute():
        parser.error("실제 전송에는 --ledger로 기존 공유 장부 절대 경로를 명시하십시오")
    ledger = args.ledger.resolve()
    expected = (ROOT / "03_실행데이터/실제연구/호출예산.sqlite3").resolve()
    if ledger != expected or not ledger.is_file():
        parser.error("기존 공유 장부 경로가 아니거나 장부가 없습니다. 새 장부를 만들지 않습니다")
    # 이 지점 전에는 제공자 모듈조차 가져오지 않는다. 검사기는 읽기 전용이다.
    # 부모가 명시 승인 후 실행하는 경로이며 대역 테스트는 이 분기를 실행하지 않는다.
    from 서버.연구앱 import _validate_existing_ledger

    try:
        _validate_existing_ledger(ledger)
    except ValueError:
        parser.error("기존 공유 장부 정책 검증 실패; 새 예산/장부를 만들지 않습니다")
    import uuid

    work = cache_path(WORK / ("live-" + uuid.uuid4().hex))
    work.mkdir(parents=True, exist_ok=False)
    prepare(work=work)
    from 서버.실제모델 import OpenAIModel

    # 생성자 오류도 원문/traceback을 공개하지 않는다. 키는 기존 생성자 안에서만 읽는다.
    try:
        model = OpenAIModel(ROOT / ".env", ledger)
    except Exception as initialization_error:  # noqa: BLE001 - 인증 오류 원문 대신 허용 메타데이터만 기록한다.
        result = {
            "passed": False,
            "live_verified": False,
            "error": "provider_initialization_failed",
            "observed_attempts": 0,
            "provider_error": safe_failure(initialization_error, []),
        }
        save(work, "실행메타데이터.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 1
    result = asyncio.run(run_probe(model=model, work=work))
    result["mode"] = "live"
    result["live_verified"] = result["passed"]
    save(work, "실행메타데이터.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


class ProbeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def safe_failure(error, attempts):
    """인증/응답 원문을 노출하지 않고 이미 관측한 안전한 원인만 보존한다."""
    allowed = {
        "configuration_error",
        "invalid_request",
        "budget_exceeded",
        "ledger_error",
        "attempt_callback_failed",
        "timeout",
        "provider_http",
        "provider_error",
        "invalid_response",
        "input_too_large",
    }
    code = getattr(error, "code", None)
    result = {"code": code if isinstance(code, str) and code in allowed else "unclassified"}
    status = getattr(error, "status", None)
    if type(status) is int and 100 <= status <= 599:
        result["status"] = status
    attempt_id = getattr(error, "attempt_id", None)
    if isinstance(attempt_id, str) and attempt_id in {item["attempt_id"] for item in attempts}:
        result["attempt_id"] = attempt_id
    return result


def usage_snapshot(model):
    value = model.usage()
    keys = (
        "attempt_count",
        "remaining_attempts",
        "spent_usd",
        "reserved_usd",
        "input_tokens",
        "output_tokens",
        "unknown_count",
    )
    return {k: v for k, v in value.items() if k in keys and type(v) in (int, float)}


async def run_probe(*, model, work=WORK):
    """명시 주입 모델만 사용한다. None일 때 실제 제공자로 대체하지 않는다."""
    from 서버.검토문맥 import build_review_request, parse_review
    from 서버.교안연구 import ResearchContractError, assemble_report, synthesis_prompt
    from 서버.보고서근거 import build_draft_request, materialize_section
    from 서버.보고서품질 import (
        parse_synthesis,
        visible_markdown,
    )

    if model is None:
        raise ValueError("explicit_model_required")
    work = cache_path(work)
    result = {
        "passed": False,
        "negative_detected": False,
        "observed_attempts": 0,
        "source_hashes": manifest(),
        "live_verified": False,
        "draft_protocol": None,
        "draft_schema_received": False,
        "synthesis_protocol": SYNTHESIS_PROTOCOL,
        "synthesis_schema_received": False,
        "review_protocol": "source-context-review-v1",
        "review_schema_cases": [],
        "control_protocol": CONTROL_PROTOCOL,
        "generated_candidate_semantic_verified": False,
    }
    attempts, receipts = [], []
    stage = "setup"

    async def call(operation, case, prompt, *, response_schema=None, request_inputs=None):
        nonlocal stage
        stage = case
        if len(attempts) >= MAX_CALLS:
            raise ProbeError("attempt_limit")
        system, user = prompt
        schema_bytes = (
            len(json.dumps(response_schema, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if response_schema is not None
            else 0
        )
        if len(system.encode()) + len(user.encode()) + schema_bytes + 8192 > MAX_INPUT_PROXY:
            raise ProbeError("input_too_large")
        slot = []
        context = {
            "run_id": work.name,
            "revision": 1,
            "actor": "quality-preflight",
            "operation": operation,
            "case": case,
        }
        save(
            work,
            f"입력_{case}.json",
            {
                "system": system,
                "user": user,
                "context": context,
                "max_output_tokens": MAX_OUTPUT,
                "json_mode": True,
                "response_schema": response_schema,
                "request_inputs": request_inputs,
            },
        )

        def on_attempt(attempt_id):
            attempts.append({"case": case, "attempt_id": attempt_id})
            save(work, "시도.json", attempts)
            slot.append(attempt_id)
            if (
                len(attempts) > MAX_CALLS
                or len(slot) != 1
                or not isinstance(attempt_id, str)
                or not attempt_id
            ):
                raise ProbeError("attempt_callback_contract")

        schema_options = {"response_schema": response_schema} if response_schema is not None else {}
        response = await model.complete(
            system,
            user,
            context=context,
            max_output_tokens=MAX_OUTPUT,
            json_mode=True,
            on_attempt=on_attempt,
            **schema_options,
        )
        if len(slot) != 1 or response.get("attempt_id") != slot[0]:
            raise ProbeError("attempt_binding_mismatch")
        if response.get("model") != MODEL:
            raise ProbeError("unexpected_model")
        if case == "section" and response_schema is not None:
            result["draft_schema_received"] = True
            result["draft_schema_sha256"] = hashlib.sha256(
                json.dumps(response_schema, ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
        if case == "synthesis" and response_schema is not None:
            result["synthesis_schema_received"] = True
            result["synthesis_schema_sha256"] = hashlib.sha256(
                json.dumps(response_schema, ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
        if operation == "semantic_review" and response_schema is not None:
            result["review_schema_cases"].append(case)
        receipt = {
            k: response.get(k)
            for k in ("attempt_id", "request_id", "model", "usage", "estimated_cost_usd")
        }
        receipts.append({"case": case, **receipt})
        save(work, "호출영수증.json", receipts)
        raw = response["content"]
        save(work, f"응답_{case}.json", {"content": raw})
        return raw

    try:
        result["usage_before"] = usage_snapshot(model)
        corpus, data = inputs()
        save(work, "입력준비.json", data)
        sources = {"임오군란": corpus.docs["임오군란"]}
        request = build_draft_request(data["assignment"], data["notes"], sources)
        result["draft_protocol"] = request.inputs["draft_protocol"]
        save(
            work,
            "입력준비.json",
            {
                **data,
                "system": request.prompts[0],
                "user": request.prompts[1],
                "request_inputs": request.inputs,
                "response_schema": request.response_schema,
            },
        )
        raw = await call(
            "draft_section", "section", request.prompts, response_schema=request.response_schema
        )
        parsed = materialize_section(raw, request, sources)
        if not parsed["충분"]:
            raise ProbeError("section_insufficient")
        first_date = parsed["본문"].find("1882년")
        if (
            first_date < 0
            or "1882년" not in visible_markdown(parsed["본문"])
            or "1882년" not in visible_markdown(parsed["본문"][: first_date + len("1882년")])
        ):
            raise ProbeError("canonical_date_missing")
        section = {**data["assignment"], **parsed, "읽은문서": ["임오군란"]}
        save(work, "절_조립.json", {"section": section, "request_inputs": request.inputs})
        sections = [section]
        title = "임오군란 발생 시기 사전점검"
        summary = parse_synthesis(
            await call(
                "synthesize",
                "synthesis",
                synthesis_prompt(
                    "임오군란의 발생 시기를 원문 근거로 요약하라. 이 사전점검은 표 1개와 SVG 도식 1개를 확인한다. 검증된 발생 시기만 요약하고 인과나 새 수치를 만들지 마라. 근거가 부족하면 생략 사유를 밝혀라.",
                    title,
                    sections,
                ),
                response_schema=build_synthesis_schema(sources),
                request_inputs={
                    "synthesis_protocol": SYNTHESIS_PROTOCOL,
                    "source_ids": sorted(sources),
                },
            ),
            sources,
        )
        if not summary["표"] or not summary["도식"]:
            raise ProbeError("table_and_svg_required_for_probe")
        positive = assemble_report(
            title, summary["머리말"], summary["맺음말"], sections, corpus, summary
        )
        negative_body = section["본문"].replace("1882년", "1883년", 1)
        negative = positive.replace(section["본문"], negative_body, 1)
        if negative == positive or "1883년" not in negative_body or "1882년" not in section["본문"]:
            raise ProbeError("date_mutation_failed")
        original_visible = visible_markdown(positive)
        negative_visible = visible_markdown(negative)
        if (
            negative_visible.count("1883년") != original_visible.count("1883년") + 1
            or negative_visible.count("1882년") != original_visible.count("1882년") - 1
        ):
            raise ProbeError("date_mutation_not_visible")
        save(
            work,
            "후보.json",
            {
                "positive": positive,
                "negative": negative,
                "evidence": [{"section_id": section["section_id"], "근거": section["근거"]}],
            },
        )
        provenance, cases, qualified_sources = control_cases(corpus)
        result["control_source"] = provenance
        positive, negative = (cases[name]["draft"] for name in ("positive", "negative"))
        sections = cases["positive"]["sections"]
        negative_sections = cases["negative"]["sections"]
        negative_visible = visible_markdown(negative)
        save(
            work,
            "대조.json",
            {
                "control_protocol": CONTROL_PROTOCOL,
                "control_source": provenance,
                "generated_candidate_semantic_verified": False,
                "positive": positive,
                "negative": negative,
                "sections": sections,
                "negative_sections": negative_sections,
            },
        )
        positive_request = build_review_request(positive, sections, qualified_sources)
        verdict = parse_review(
            await call(
                "semantic_review",
                "positive",
                positive_request.prompts,
                response_schema=positive_request.response_schema,
                request_inputs=positive_request.inputs,
            ),
            positive_request,
            qualified_sources,
        )
        if verdict["통과"] is not True:
            raise ProbeError("positive_rejected")
        # 음성의 본문/주장만 1883년이며 원문과 선택 인용은 양성과 동일하다.
        negative_request = build_review_request(negative, negative_sections, qualified_sources)
        verdict = parse_review(
            await call(
                "semantic_review",
                "negative",
                negative_request.prompts,
                response_schema=negative_request.response_schema,
                request_inputs=negative_request.inputs,
            ),
            negative_request,
            qualified_sources,
        )
        if verdict["통과"] is not False:
            raise ProbeError("negative_accepted")
        # 단순 fail-closed/다른 지적을 날짜 검출 성공으로 계산하지 않는다.
        if not any(
            p["유형"] == "contradiction"
            and "1883년" in p["본문발췌"]
            and p["본문발췌"] in negative_request.inputs["visible_text"]
            and "임오군란" in p["출처"]
            for p in verdict["문제"]
        ):
            raise ProbeError("negative_date_not_identified")
        result.update(passed=True, negative_detected=True)
    except ResearchContractError:
        result["error"] = "invalid_schema"
    except ProbeError as error:
        result["error"] = error.code
    except Exception as provider_error:  # noqa: BLE001 - 제공자 원문은 버리고 허용 코드/상태/시도만 남긴다.
        # 제공자 오류 원문/traceback에는 인증/원시 응답이 있을 수 있다.
        result["error"] = "provider_or_runtime_error"
        result["provider_error"] = safe_failure(provider_error, attempts)
    finally:
        result["stage"] = stage
        result["observed_attempts"] = len(attempts)
        result["completed_calls"] = len(receipts)
        try:
            result["usage_after"] = usage_snapshot(model)
        except Exception as usage_error:  # noqa: BLE001 - 정리 실패가 원래 제공자 실패를 덮지 않게 한다.
            result["passed"] = False
            result.setdefault("error", "usage_unavailable")
            result["usage_error"] = safe_failure(usage_error, attempts)
        try:
            await model.aclose()
        except Exception as close_error:  # noqa: BLE001 - 소유 클라이언트 정리 실패도 안전하게 보존한다.
            result["passed"] = False
            result.setdefault("error", "close_failed")
            result["close_error"] = safe_failure(close_error, attempts)
        save(work, "호출영수증.json", receipts)
        save(work, "실행메타데이터.json", result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
