"""실제 제공자 없이 preflight 안전 경계와 기존 순수 파서를 검사한다."""

import asyncio
import hashlib
import html
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
sys.path.insert(0, str(SPIKE))
SPEC = importlib.util.spec_from_file_location(
    "quality_preflight", SPIKE / "검증/보고서품질_실모델점검.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture(autouse=True)
def no_credentials_ledger_or_provider(monkeypatch, tmp_path):
    # 기본 CLI prepare도 이 테스트의 격리 경로로 제한한다.
    monkeypatch.setitem(probe.prepare.__kwdefaults__, "work", tmp_path)
    monkeypatch.setitem(probe.run_probe.__kwdefaults__, "work", tmp_path)
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


def test_preflight_manifest_binds_both_evidence_repair_modules(monkeypatch):
    monkeypatch.setattr(Path, "read_bytes", lambda _path: b"isolated module bytes")
    paths = probe.manifest()
    assert str((SPIKE / "서버/근거보완.py").resolve()) in paths
    assert str((SPIKE / "서버/보고서보완.py").resolve()) in paths


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
        self.positive = {
            "검토목록ID": "__REQUEST__",
            "판정": "supported",
            "문제": [],
            "검토요약": "대역의 양성 응답",
        }
        self.negative = {
            "검토목록ID": "__REQUEST__",
            "판정": "needs_revision",
            "문제": [
                {
                    "유형": "contradiction",
                    "설명": "1883년은 원문의 1882년과 다르다.",
                    "본문발췌": "1883년",
                    "근거번호": ["__QUOTE__"],
                }
            ],
            "검토요약": "대역의 음성 응답",
        }
        self.responses = [self.section, self.synthesis, self.positive, self.negative]
        self.extra_fields = {}
        self.bad_reference = False
        self.overrides = overrides or {}
        self.calls, self.attempts, self.closed = [], [], False
        self.callbacks = callbacks

    async def complete(
        self,
        system,
        user,
        *,
        context,
        max_output_tokens,
        json_mode,
        on_attempt,
        response_schema=None,
    ):
        index = len(self.calls)
        self.calls.append(
            {
                "system": system,
                "user": user,
                "context": context,
                "max_output_tokens": max_output_tokens,
                "json_mode": json_mode,
                "response_schema": response_schema,
            }
        )
        attempt = f"unbound-{index}"
        for n in range(self.callbacks):
            attempt = f"fake-{index}-{n}"
            self.attempts.append(attempt)
            on_attempt(attempt)
        reply = self.overrides.get(index, self.responses[index])
        if index == 0 and index not in self.overrides:
            payload = json.loads(user)
            offered = payload["evidence"]
            assert offered, "명시 대역도 실제 요청에 제시된 구간만 선택한다"
            anchor = self.section["근거"][0]["인용문"]
            chosen = next((span for span in offered if anchor in span["text"]), offered[0])
            ref = chosen["id"]
            if self.bad_reference:
                ref = 999999
                assert ref not in {span["id"] for span in offered}
            text = self.section["본문"].replace("«임오군란»", "").rstrip()
            reply = {
                "근거목록ID": payload["evidence_set_id"],
                "문장": [text],
                "근거관계": [{"문장번호": 1, "근거구간": ref, **self.extra_fields}],
                "충분": self.section["충분"],
                "부족": self.section["부족"],
            }
        if context["operation"] == "semantic_review":
            payload = json.loads(user)
            assert response_schema["properties"]["검토목록ID"]["enum"] == [payload["review_set_id"]]
            assert payload["contexts"] and payload["source_hashes"]
            # 실제 요청에 결합하는 명시 대역이다. bool-only reply를 변환하는 fallback은 없다.
            if isinstance(reply, dict) and reply.get("검토목록ID") == "__REQUEST__":
                reply = json.loads(json.dumps(reply, ensure_ascii=False))
                reply["검토목록ID"] = payload["review_set_id"]
                for problem in reply["문제"]:
                    if problem["근거번호"] == ["__QUOTE__"]:
                        problem["근거번호"] = [
                            next(
                                c["id"]
                                for c in payload["contexts"]
                                if c["kind"] == "quote" and "1882년" in c["text"]
                            )
                        ]
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


def test_generated_inference_does_not_contaminate_source_fixed_controls(tmp_path):
    expected = "임오군란은 1882년 7월 23일부터 8월 30일까지 이어진 조선 한성부에서 발발한 구식 군인의 폭동이다."
    inference = "이후의 모든 갈등도 완전히 끝났다는 추가 해석이다."
    model = ModelDouble()
    model.section["본문"] = expected + " " + inference + " «임오군란»"
    model.synthesis["맺음말"] += " " + inference
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"], result
    positive, negative = [json.loads(c["user"]) for c in model.calls[2:]]
    assert inference not in positive["draft"], "generated inference contaminated known-positive"
    assert inference not in negative["draft"]
    assert positive["evidence"] == [
        {
            "section_id": "control-1",
            "근거": [{"주장": expected, "출처": "임오군란", "인용문": expected}],
        }
    ]
    assert negative["evidence"][0]["근거"] == [
        {"주장": expected.replace("1882년", "1883년", 1), "출처": "임오군란", "인용문": expected}
    ]
    corpus, _ = probe.inputs()
    provenance = {
        "source_id": "임오군란",
        "source_sha256": hashlib.sha256(corpus.docs["임오군란"].encode("utf-8")).hexdigest(),
        "start": 0,
        "end": 59,
        "quote": expected,
        "positive_claim": expected,
        "negative_claim": expected.replace("1882년", "1883년", 1),
    }
    assert result["control_protocol"] == "canonical-source-date-v1"
    assert result["control_source"] == provenance
    assert result["generated_candidate_semantic_verified"] is False
    controls = json.loads((tmp_path / "대조.json").read_text("utf-8"))
    assert controls["control_source"] == provenance
    assert controls["positive"] == positive["draft"]
    assert controls["negative"] == negative["draft"]
    assert positive["source_hashes"] == negative["source_hashes"]
    generated = json.loads((tmp_path / "후보.json").read_text("utf-8"))
    assert inference in generated["positive"] and "svg\n" in generated["positive"]
    assert result["observed_attempts"] == result["completed_calls"] == 4 and model.closed


def test_probe_metadata_rejects_reuse_of_nested_reference_protocol(tmp_path):
    result = asyncio.run(probe.run_probe(model=ModelDouble(), work=tmp_path))
    assert result["passed"] is True
    assert result["draft_protocol"] == "sentence-paragraph-evidence-v3"


def test_active_preflight_sends_id_catalog_and_never_asks_model_to_copy_quotes(tmp_path):
    model = ModelDouble()
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    first = model.calls[0]
    assert first["user"].lstrip().startswith("{"), (
        "초안의 실제 전송 입력은 구간 목록 JSON이어야 한다"
    )
    payload = json.loads(first["user"])
    assert {"assignment", "notes", "evidence_set_id", "evidence"} == set(payload)
    assert payload["evidence"] and all(
        {"id", "source_id", "start", "end", "text"} == set(span) for span in payload["evidence"]
    )
    assert result["passed"] is True and result["observed_attempts"] == 4
    response = json.loads((tmp_path / "응답_section.json").read_text("utf-8"))["content"]
    assert {"근거목록ID", "문장", "근거관계", "충분", "부족"} == set(json.loads(response))


def test_actual_draft_call_forwards_provider_schema_not_only_prompt(tmp_path):
    class SchemaRequiredDouble(ModelDouble):
        async def complete(self, *args, response_schema=None, **kwargs):
            if kwargs["context"]["operation"] == "draft_section":
                assert isinstance(response_schema, dict), (
                    "실제 모델 호출에 strict schema를 전달해야 한다"
                )
                assert response_schema["additionalProperties"] is False
                payload = json.loads(args[1])
                properties = response_schema["properties"]
                assert properties["근거목록ID"]["enum"] == [payload["evidence_set_id"]]
                assert properties["근거관계"]["maxItems"] == 16
                assert properties["문장"]["items"] == {"type": "string"}
                assert properties["근거관계"]["items"]["properties"]["근거구간"] == {
                    "type": "integer",
                    "enum": [span["id"] for span in payload["evidence"]],
                }
            return await super().complete(*args, response_schema=response_schema, **kwargs)

    model = SchemaRequiredDouble()
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is True and result["observed_attempts"] == 4
    assert result["draft_protocol"] == "sentence-paragraph-evidence-v3"
    assert result["draft_schema_received"] is True
    assert result["review_protocol"] == "source-context-review-v1"
    assert result["review_schema_cases"] == ["positive", "negative"]
    for index, case in [(2, "positive"), (3, "negative")]:
        saved_review = json.loads((tmp_path / f"입력_{case}.json").read_text("utf-8"))
        catalog = json.loads(model.calls[index]["user"])
        assert saved_review["request_inputs"] == catalog
        assert saved_review["response_schema"] == model.calls[index]["response_schema"]
        assert saved_review["response_schema"]["properties"]["검토목록ID"]["enum"] == [
            catalog["review_set_id"]
        ]
    saved = json.loads((tmp_path / "입력_section.json").read_text("utf-8"))
    assert (
        result["draft_schema_sha256"]
        == hashlib.sha256(
            json.dumps(saved["response_schema"], ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        ).hexdigest()
    )


def test_종합사전요청은_동일strict_schema와_출처저널과_해시를_보존한다(tmp_path):
    from 서버.종합계약 import build_synthesis_schema

    model = ModelDouble()
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"], result
    sent = model.calls[1]
    assert sent["response_schema"] is not None, "사전 종합 schema 누락"
    assert sent["response_schema"] == build_synthesis_schema(["임오군란"])
    saved = json.loads((tmp_path / "입력_synthesis.json").read_text("utf-8"))
    assert saved["response_schema"] == sent["response_schema"]
    assert saved["request_inputs"] == {
        "synthesis_protocol": "source-bound-synthesis-v1",
        "source_ids": ["임오군란"],
    }
    assert result["synthesis_protocol"] == "source-bound-synthesis-v1"
    assert result["synthesis_schema_received"] is True
    assert (
        result["synthesis_schema_sha256"]
        == hashlib.sha256(
            json.dumps(saved["response_schema"], ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        ).hexdigest()
    )
    assert str((SPIKE / "서버/종합계약.py").resolve()) in result["source_hashes"]
    assert result["observed_attempts"] == result["completed_calls"] == 4
    assert result["generated_candidate_semantic_verified"] is False


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
    assembled = json.loads((tmp_path / "절_조립.json").read_text("utf-8"))["section"]["본문"]
    rendered = MarkdownIt("commonmark", {"html": False}).render(assembled)
    visible = html.unescape(re.sub(r"<[^>]+>", "", rendered))
    assert " ".join(visible.split()) == " ".join(model.section["본문"].split())
    assert assembled in model.calls[1]["user"]
    assert model.quote in model.calls[1]["user"]
    assert "표 1개와 SVG 도식 1개" in model.calls[1]["user"]
    candidates = json.loads((tmp_path / "후보.json").read_text(encoding="utf-8"))
    assert "| 사건 | 연도 | 출처 |" in candidates["positive"]
    assert "svg\n" in candidates["positive"]
    assert "<title>발생 시기</title>" in candidates["positive"]
    assert assembled in candidates["positive"]
    assert candidates["positive"] != candidates["negative"]
    assert "1883년" in candidates["negative"]
    negative_review = json.loads(model.calls[3]["user"])
    assert any("1882년 7월 23일" in c["text"] for c in negative_review["contexts"])
    assert "1883년" in negative_review["visible_text"]
    assert "1883년" not in json.loads(model.calls[2]["user"])["visible_text"]
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
        (
            3,
            {"검토목록ID": "__REQUEST__", "판정": "supported", "문제": [], "검토요약": "잘못 통과"},
            "negative_accepted",
            4,
        ),
        (
            3,
            {"검토목록ID": "__REQUEST__", "판정": "needs_revision", "문제": [], "검토요약": "모순"},
            "invalid_schema",
            4,
        ),
        (
            3,
            {
                "검토목록ID": "__REQUEST__",
                "판정": "needs_revision",
                "문제": [
                    {
                        "유형": "unsupported",
                        "설명": "다른 지적",
                        "본문발췌": "임오군란",
                        "근거번호": [],
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


def test_실제검토요청의_제목포함_다중행발췌도_연도모순검출로_인정한다(tmp_path):
    model = ModelDouble()
    model.negative["문제"][0]["본문발췌"] = (
        "임오군란 날짜 대조\n"
        "임오군란은 1883년 7월 23일부터 8월 30일까지 이어진 조선 한성부에서 발발한 "
        "구식 군인의 폭동이다. «임오군란»\n"
    )
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] and result["negative_detected"], result
    assert result["observed_attempts"] == 4 and model.closed


def test_negative_date_mention_without_date_contradiction_is_not_detection(tmp_path):
    model = ModelDouble()
    model.negative["문제"][0]["유형"] = "unsupported"
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is False
    assert result["error"] == "negative_date_not_identified"
    assert result["negative_detected"] is False
    assert result["observed_attempts"] == 4 and model.closed


def test_positive_rejection_stops_before_negative(tmp_path):
    model = ModelDouble()
    model.overrides[2] = json.loads(json.dumps(model.negative, ensure_ascii=False))
    model.overrides[2]["문제"][0]["본문발췌"] = "1882년"
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


def test_negative_date_change_must_change_visible_body_not_only_link_destination(
    tmp_path, monkeypatch
):
    from 서버 import 보고서근거

    original = 보고서근거.materialize_section

    def canonical_with_hidden_first_date(*args, **kwargs):
        section = original(*args, **kwargs)
        return {**section, "본문": "[참고](https://example.invalid/1882년) " + section["본문"]}

    # 새 조립기는 hidden URL을 제거한다. oracle의 원래 반례는 조립된 문서 경계에 유지한다.
    monkeypatch.setattr(보고서근거, "materialize_section", canonical_with_hidden_first_date)
    model = ModelDouble()
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is False
    assert result["error"] == "date_mutation_not_visible"
    assert result["observed_attempts"] == 2 and model.closed


def test_real_observed_visible_excerpt_matches_even_when_canonical_period_is_escaped(tmp_path):
    model = ModelDouble()
    model.negative["문제"][0]["본문발췌"] = model.quote.replace("1882년", "1883년", 1)
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is True and result["negative_detected"] is True
    assert result["observed_attempts"] == 4 and model.closed


def test_new_materializer_drops_hidden_date_before_real_visible_date_mutation(tmp_path):
    model = ModelDouble()
    model.section["본문"] = "[참고](https://example.invalid/1882년) " + model.section["본문"]
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    candidates = json.loads((tmp_path / "후보.json").read_text("utf-8"))
    assert "https://example.invalid" not in candidates["positive"]
    assert "참고" in candidates["positive"]
    assert result["passed"] and result["negative_detected"]
    assert result["observed_attempts"] == 4 and model.closed


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
    # 새 모델은 복사 필드를 쓰지 않는다. 유효 ID 옆의 임의 복사 필드도 수용하지 않는다.
    model.extra_fields[field] = value
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "invalid_schema"
    assert len(model.calls) == 1 and model.closed


def test_unoffered_span_stops_before_synthesis_without_new_calls(tmp_path):
    model = ModelDouble()
    model.bad_reference = True
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["passed"] is False and result["error"] == "invalid_schema"
    assert result["observed_attempts"] == 1 and len(model.calls) == 1 and model.closed


def test_exact_catalog_snapshot_is_saved_before_first_model_attempt(tmp_path):
    class SnapshotDouble(ModelDouble):
        snapshot_seen = False

        async def complete(self, *args, **kwargs):
            if not self.calls:
                saved = json.loads((tmp_path / "입력준비.json").read_text("utf-8"))
                assert saved.get("request_inputs", {}).get("source_hashes"), (
                    "전송 전에 원문 버전과 제시 목록을 보존해야 한다"
                )
                assert json.loads(saved["user"])["evidence"]
                self.snapshot_seen = True
            return await super().complete(*args, **kwargs)

    model = SnapshotDouble()
    model.bad_reference = True
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    assert result["error"] == "invalid_schema" and result["observed_attempts"] == 1
    assert model.snapshot_seen and model.closed


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
