"""실제 제공자 없이 preflight 안전 경계와 기존 순수 파서를 검사한다."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
sys.path.insert(0, str(SPIKE))
SPEC = importlib.util.spec_from_file_location(
    "quality_preflight", SPIKE / "검증/보고서품질_실모델점검.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture(autouse=True)
def no_credentials_ledger_or_provider(monkeypatch):
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name != ".env", "테스트에서 실제 인증 접근 금지"
        assert path.suffix != ".sqlite3", "테스트에서 장부 접근 금지"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setitem(sys.modules, "서버.실제모델", None)
    monkeypatch.setitem(sys.modules, "서버.연구앱", None)


def test_prepare_never_reads_secrets_or_ledger_or_constructs_provider(monkeypatch, tmp_path):
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name != ".env"
        assert path.suffix != ".sqlite3"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setitem(sys.modules, "서버.실제모델", None)
    monkeypatch.setitem(sys.modules, "서버.연구앱", None)
    result = probe.prepare(work=tmp_path)
    assert result["mode"] == "prepare"
    assert result["observed_attempts"] == 0
    assert result["live_verified"] is False
    assert result["max_cost_usd"] == 0.2916608
    assert result["source_key"] == "임오군란"
    assert len(result["source_text_sha256"]) == 64
    data = json.loads((tmp_path / "입력준비.json").read_text(encoding="utf-8"))
    assert data["quote"].startswith("임오군란은 1882년 7월 23일부터")
    assert "사전점검" in data["notes"][0][1]
    assert "LLM 읽기 결과가 아님" in data["notes"][0][1]


class ModelDouble:
    """명시적 스크립트 응답: 의미 검출력을 구현하거나 보증하지 않는다."""

    def __init__(self, overrides=None, callbacks=1):
        corpus, data = probe.inputs()
        self.quote = data["quote"]
        body = corpus.docs["임오군란"].split("== 전개 ==", 1)[0].strip() + " «임오군란»"
        self.section = {
            "본문": body,
            "충분": True,
            "부족": "",
            "근거": [{"주장": self.quote, "출처": "임오군란", "인용문": self.quote}],
        }
        self.synthesis = {
            "머리말": "임오군란 발생 시기를 확인한다.",
            "맺음말": "제시된 근거 범위의 정리다.",
            "표": [
                {
                    "제목": "발생 연도",
                    "열": ["사건", "연도"],
                    "행": [{"셀": ["임오군란", "1882년"], "출처": ["임오군란"]}],
                }
            ],
            "도식": [
                {
                    "제목": "발생 시기",
                    "설명": "임오군란의 발생 연도",
                    "코드": '<svg viewBox="0 0 200 40"><title>발생 시기</title><text x="5" y="20">임오군란 1882년</text></svg>',
                    "출처": ["임오군란"],
                }
            ],
            "시각화생략사유": "",
        }
        self.positive = {"통과": True, "문제": [], "검토요약": "대역의 양성 응답"}
        self.negative = {
            "통과": False,
            "문제": [
                {
                    "유형": "날짜",
                    "설명": "1883년은 원문의 1882년과 다르다.",
                    "본문발췌": "1883년",
                    "출처": ["임오군란"],
                }
            ],
            "검토요약": "대역의 음성 응답",
        }
        self.responses = [self.section, self.synthesis, self.positive, self.negative]
        self.overrides = overrides or {}
        self.calls, self.attempts, self.closed = [], [], False
        self.callbacks = callbacks

    async def complete(self, system, user, *, context, max_output_tokens, json_mode, on_attempt):
        index = len(self.calls)
        self.calls.append(
            {
                "system": system,
                "user": user,
                "context": context,
                "max_output_tokens": max_output_tokens,
                "json_mode": json_mode,
            }
        )
        attempt = f"unbound-{index}"
        for n in range(self.callbacks):
            attempt = f"fake-{index}-{n}"
            self.attempts.append(attempt)
            on_attempt(attempt)
        reply = self.overrides.get(index, self.responses[index])
        if isinstance(reply, Exception):
            raise reply
        return {
            "content": reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False),
            "model": "gpt-5.6-luna",
            "attempt_id": attempt,
            "request_id": f"fake-request-{index}",
            "usage": {"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 0},
            "estimated_cost_usd": 0.000085,
        }

    def usage(self):
        return {
            "attempt_count": len(self.attempts),
            "input_tokens": len(self.calls) * 100,
            "output_tokens": len(self.calls) * 50,
            "spent_usd": len(self.calls) * 0.000085,
        }

    async def aclose(self):
        self.closed = True


def test_four_steps_use_actual_parsers_and_assembly_with_bound_attempts(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "서버.실제모델", None)
    model = ModelDouble()
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is True
    assert model.closed
    assert result["observed_attempts"] == 4
    assert result["usage_before"]["attempt_count"] == 0
    assert result["usage_after"]["attempt_count"] == 4
    assert [c["context"]["operation"] for c in model.calls] == [
        "draft_section",
        "synthesize",
        "semantic_review",
        "semantic_review",
    ]
    assert [c["context"]["case"] for c in model.calls] == [
        "section",
        "synthesis",
        "positive",
        "negative",
    ]
    assert all(c["max_output_tokens"] == 4096 and c["json_mode"] is True for c in model.calls)
    assert model.section["본문"] in model.calls[1]["user"]
    assert model.quote in model.calls[1]["user"]
    assert "표 1개와 SVG 도식 1개" in model.calls[1]["user"]
    candidates = json.loads((tmp_path / "후보.json").read_text(encoding="utf-8"))
    assert "| 사건 | 연도 | 출처 |" in candidates["positive"]
    assert "svg\n" in candidates["positive"]
    assert "<title>발생 시기</title>" in candidates["positive"]
    assert model.section["본문"] in candidates["positive"]
    assert candidates["positive"] != candidates["negative"]
    assert "1883년" in candidates["negative"]
    assert "1882년 7월 23일" in model.calls[3]["user"].split("[검증된 주장과 원문 인용구]", 1)[1]
    assert "1883년" not in model.calls[2]["user"]
    assert result["negative_detected"] is True
    receipts = json.loads((tmp_path / "호출영수증.json").read_text(encoding="utf-8"))
    assert [c["attempt_id"] for c in receipts] == [f"fake-{n}-0" for n in range(4)]
    assert len(result["source_hashes"]) >= 10


@pytest.mark.parametrize(
    "index,reply,error,expected_calls",
    [
        (0, "not JSON", "invalid_schema", 1),
        (1, "not JSON", "invalid_schema", 2),
        (2, "not JSON", "invalid_schema", 3),
        (3, "not JSON", "invalid_schema", 4),
        (0, RuntimeError("DO-NOT-LOG-provider-secret"), "provider_or_runtime_error", 1),
        (1, RuntimeError("DO-NOT-LOG-provider-secret"), "provider_or_runtime_error", 2),
        (2, RuntimeError("DO-NOT-LOG-provider-secret"), "provider_or_runtime_error", 3),
        (3, {"통과": True, "문제": [], "검토요약": "잘못 통과"}, "negative_accepted", 4),
        (3, {"통과": False, "문제": [], "검토요약": "모순"}, "invalid_schema", 4),
        (
            3,
            {
                "통과": False,
                "문제": [
                    {
                        "유형": "기타",
                        "설명": "다른 지적",
                        "본문발췌": "임오군란",
                        "출처": ["임오군란"],
                    }
                ],
                "검토요약": "날짜 미검출",
            },
            "negative_date_not_identified",
            4,
        ),
    ],
)
def test_stops_without_retry_or_false_detection(index, reply, error, expected_calls, tmp_path):
    model = ModelDouble({index: reply})
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is False
    assert result["negative_detected"] is False
    assert result["error"] == error
    assert len(model.calls) == expected_calls
    assert result["observed_attempts"] == expected_calls
    assert result["usage_after"]["attempt_count"] == expected_calls
    assert model.closed
    assert "DO-NOT-LOG-provider-secret" not in (tmp_path / "실행메타데이터.json").read_text(
        encoding="utf-8"
    )
    assert len(model.calls) <= 4


def test_positive_rejection_stops_before_negative(tmp_path):
    model = ModelDouble()
    model.overrides[2] = model.negative
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "positive_rejected"
    assert result["passed"] is False and result["negative_detected"] is False
    assert len(model.calls) == 3 and model.closed


@pytest.mark.parametrize("known", [True, False])
def test_provider_failure_keeps_only_safe_code_status_and_bound_attempt(known, tmp_path):
    error = RuntimeError("DO-NOT-LOG-provider-secret")
    error.code = "provider_http" if known else "DO-NOT-LOG-provider-secret"
    error.status = 429 if known else "DO-NOT-LOG-provider-secret"
    error.attempt_id = "fake-0-0" if known else "DO-NOT-LOG-provider-secret"
    model = ModelDouble({0: error})
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    expected = (
        {"code": "provider_http", "status": 429, "attempt_id": "fake-0-0"}
        if known
        else {"code": "unclassified"}
    )
    assert result["provider_error"] == expected
    assert result["passed"] is False and result["observed_attempts"] == 1
    assert model.closed
    assert "DO-NOT-LOG-provider-secret" not in (tmp_path / "실행메타데이터.json").read_text("utf-8")


def test_negative_date_change_must_change_visible_body_not_only_link_destination(tmp_path):
    model = ModelDouble()
    model.section["본문"] = "[참고](https://example.invalid/1882년) " + model.section["본문"]
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is False
    assert result["error"] == "date_mutation_not_visible"
    assert result["observed_attempts"] == 2 and model.closed


@pytest.mark.parametrize(
    "field,value",
    [
        ("출처", "갑신정변"),
        ("인용문", "원문에 전혀 없는 조작 인용문"),
        ("주장", "본문에 전혀 없는 주장"),
    ],
)
def test_actual_evidence_contract_rejects_tampering(field, value, tmp_path):
    model = ModelDouble()
    model.section["근거"][0][field] = value
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "invalid_schema"
    assert len(model.calls) == 1 and model.closed


def test_actual_svg_validator_rejects_unsafe_code(tmp_path):
    model = ModelDouble()
    model.synthesis["도식"][0]["코드"] = '<svg viewBox="0 0 1 1"><script>bad</script></svg>'
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "invalid_schema"
    assert len(model.calls) == 2 and model.closed


def test_multiple_attempts_are_not_counted_as_one_logical_call(tmp_path):
    model = ModelDouble(callbacks=2)
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "attempt_callback_contract"
    assert result["observed_attempts"] == 2
    assert len(model.calls) == 1 and model.closed
    assert result["usage_after"]["attempt_count"] == 2


def test_missing_callback_does_not_fall_back_to_call_count(tmp_path):
    model = ModelDouble(callbacks=0)
    # 예약 callback 없는 응답은 함수가 반환되어도 실제 시도로 셀 수 없다.
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is False
    assert result["observed_attempts"] == 0
    assert result["error"] == "attempt_binding_mismatch"
    assert len(model.calls) == 1 and model.closed


def test_none_injection_never_constructs_provider(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "서버.실제모델", None)
    with pytest.raises(ValueError, match="explicit_model_required"):
        asyncio.run(probe.run_probe(model=None, work=tmp_path))


def test_all_output_paths_are_inside_project_cache():
    with pytest.raises(ValueError, match="output_outside_preflight_cache"):
        probe.prepare(work=ROOT / "검증결과/금지")


def test_main_default_is_prepare_without_provider(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "서버.실제모델", None)
    monkeypatch.setitem(sys.modules, "서버.연구앱", None)
    assert probe.main([]) == 0
    output = capsys.readouterr().out
    assert output.strip(), "기본 진입점은 준비 메타데이터를 출력해야 한다"
    result = json.loads(output)
    assert result["mode"] == "prepare"
    assert result["observed_attempts"] == 0


def test_date_only_in_fenced_code_cannot_be_negative_control(tmp_path):
    model = ModelDouble()
    corpus, _ = probe.inputs()
    quote = corpus.docs["임오군란"].split(". ")[1] + "."
    model.section["본문"] = "```text\n1882년\n```\n" + quote + " «임오군란»"
    model.section["근거"] = [{"주장": quote, "출처": "임오군란", "인용문": quote}]
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "canonical_date_missing"
    assert len(model.calls) == 1 and model.closed
    assert result["negative_detected"] is False


@pytest.mark.parametrize(
    "argv",
    [
        ["--allow-live"],
        ["--allow-live", "--ledger", "relative.sqlite3"],
        ["--ledger", "relative.sqlite3"],
        ["--prepare", "--allow-live"],
    ],
)
def test_cli_rejects_incomplete_live_permission_before_provider(argv):
    # 잘못된 CLI는 파서에서만 검사한다. live 분기/장부 검증/제공자는 실행하지 않는다.
    with pytest.raises(SystemExit) as error:
        probe.main(argv)
    assert error.value.code == 2


def test_explicit_prepare_cli_is_off(capsys):
    assert probe.main(["--prepare"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "prepare" and result["observed_attempts"] == 0


@pytest.mark.parametrize("missing", ["표", "도식"])
def test_missing_visual_stops_before_semantic(missing, tmp_path):
    model = ModelDouble()
    model.synthesis[missing] = []
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "table_and_svg_required_for_probe"
    assert len(model.calls) == 2 and model.closed


def test_insufficient_section_stops_before_synthesis(tmp_path):
    model = ModelDouble()
    model.section.update(충분=False, 부족="자료 부족")
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "section_insufficient"
    assert len(model.calls) == 1 and model.closed


def test_pre_send_budget_rejection_has_zero_observed_attempts(tmp_path):
    class Denied(ModelDouble):
        async def complete(self, *args, **kwargs):
            self.calls.append({"denied_before_reservation": True})
            raise RuntimeError("budget_exceeded-with-private-provider-info")

    model = Denied()
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is False and result["negative_detected"] is False
    assert result["observed_attempts"] == 0
    assert result["usage_after"]["attempt_count"] == 0
    assert len(model.calls) == 1 and model.closed


def test_reply_attempt_id_must_match_callback(tmp_path):
    class Misbound(ModelDouble):
        async def complete(self, *args, **kwargs):
            response = await super().complete(*args, **kwargs)
            response["attempt_id"] = "wrong-receipt-id"
            return response

    model = Misbound()
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "attempt_binding_mismatch"
    assert result["observed_attempts"] == 1
    assert len(model.calls) == 1 and model.closed
