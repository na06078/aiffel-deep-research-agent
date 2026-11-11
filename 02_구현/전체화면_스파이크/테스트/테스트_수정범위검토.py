"""실제 순수 교정 결과→검토 요청. 제공자 판단은 명시 응답이며 모델 성능 검증 아님."""

import copy
import json

import pytest

from 서버.검토문맥 import build_review_request, parse_review
from 서버.교안연구 import ResearchContractError
from 서버.근거보완 import apply_evidence_correction, prepare_evidence_correction
from 서버.보고서보완 import apply_correction, prepare_correction
from 테스트.테스트_근거보완 import fixture, response


def corrected(evidence=False, after="첫 사건의 기록이 있다."):
    draft, sections, problems, sources = fixture()
    if evidence:
        request = prepare_evidence_correction(draft, sections, problems, sources)
        result = apply_evidence_correction(json.dumps(response(request, after)), request, sources)
    else:
        request = prepare_correction(draft, sections, problems, sources)
        result = apply_correction(
            json.dumps({"교정": [{"대상번호": 1, "내용": after}]}), request, sources
        )
    scope = {
        "source_revision": 1,
        "original_draft": draft,
        "changes": [
            {k: c[k] for k in ("target_id", "section_id", "before", "after")}
            for c in result["changes"]
        ],
    }
    qualified = {
        s["section_id"]: {k: sources[k] for k in s["읽은문서"]} for s in result["sections"]
    }
    return result, scope, qualified


def scoped_request(result, scope, sources):
    return build_review_request(
        result["draft"], result["sections"], sources, correction_scope=scope
    )


@pytest.mark.parametrize("evidence", [False, True])
def test_actual_correction_scope_is_bound_to_system_user_hash_and_detached(evidence):
    result, scope, sources = corrected(evidence)
    normal = build_review_request(result["draft"], result["sections"], sources)
    try:
        request = scoped_request(result, scope, sources)
    except TypeError as exc:
        pytest.fail(f"approved scoped review boundary missing: {exc}")
    assert request.inputs["correction_scope"] == scope
    assert json.loads(request.prompts[1])["correction_scope"] == scope
    assert "scope_expansion" in request.prompts[0] and "수정 전후" in request.prompts[0]
    assert request.inputs["review_set_id"] != normal.inputs["review_set_id"]
    altered = copy.deepcopy(scope)
    altered["source_revision"] = 2
    assert (
        scoped_request(result, altered, sources).inputs["review_set_id"]
        != request.inputs["review_set_id"]
    )
    scope["changes"][0]["after"] = "변조"
    request.inputs["correction_scope"].clear()
    assert request.inputs["correction_scope"]["changes"][0]["after"] == "첫 사건의 기록이 있다."
    assert (
        sum(len(p.encode()) for p in request.prompts)
        + len(json.dumps(request.response_schema, ensure_ascii=False).encode())
        + 8192
        <= 272000
    )


def test_new_source_supported_fact_is_valid_negative_not_passed_or_undetermined():
    # 자료가 새 사실을 지지하더라도 허용 수정범위와는 별개다.
    result, scope, sources = corrected(
        True, "첫 사건의 기록은 남아 있다. 다음 사건은 별도로 기록되어 있다."
    )
    try:
        request = scoped_request(result, scope, sources)
    except TypeError as exc:
        pytest.fail(f"approved scoped review boundary missing: {exc}")
    problem = {
        "유형": "scope_expansion",
        "설명": "기존 대상에 없던 다음 사건을 추가함",
        "본문발췌": "다음 사건은 별도로 기록되어 있다.",
        "근거번호": [],
    }
    obj = {
        "검토목록ID": request.inputs["review_set_id"],
        "판정": "needs_revision",
        "문제": [problem],
        "검토요약": "범위 확장 차단 대역",
    }
    decision = parse_review(json.dumps(obj), request, sources)
    assert decision["통과"] is False and decision["판정"] == "needs_revision"
    assert decision["문제"][0] == {**problem, "출처": [], "근거문맥": []}
    assert (
        "scope_expansion"
        in request.response_schema["properties"]["문제"]["items"]["properties"]["유형"]["enum"]
    )
    obj["판정"] = "supported"
    with pytest.raises(ResearchContractError, match="contradictory verdict"):
        parse_review(json.dumps(obj), request, sources)
    obj["판정"] = "needs_revision"
    obj["문제"][0]["본문발췌"] = "원고에 없는 확장"
    with pytest.raises(ResearchContractError, match="excerpt not visible"):
        parse_review(json.dumps(obj), request, sources)


@pytest.mark.parametrize(
    "mutation",
    [
        "revision_bool",
        "revision_zero",
        "revision_string",
        "root_extra",
        "missing",
        "empty",
        "duplicate",
        "target_bool",
        "section",
        "after",
        "before",
        "hidden_after",
        "change_extra",
        "original_type",
    ],
)
def test_invalid_scope_rejected_before_any_provider(mutation):
    result, scope, sources = corrected()
    # 같은 실제 결과의 유효 양성 대조를 먼저 통과한다.
    try:
        scoped_request(result, scope, sources)
    except TypeError as exc:
        pytest.fail(f"approved scoped review boundary missing: {exc}")
    if mutation.startswith("revision_"):
        scope["source_revision"] = {
            "revision_bool": True,
            "revision_zero": 0,
            "revision_string": "1",
        }[mutation]
    elif mutation == "root_extra":
        scope["extra"] = 1
    elif mutation == "missing":
        del scope["original_draft"]
    elif mutation == "empty":
        scope["changes"] = []
    elif mutation == "duplicate":
        scope["changes"] *= 2
    elif mutation == "target_bool":
        scope["changes"][0]["target_id"] = True
    elif mutation == "section":
        scope["changes"][0]["section_id"] = "wrong"
    elif mutation == "after":
        scope["changes"][0]["after"] = "실제로 반영하지 않은 주장"
    elif mutation == "before":
        scope["changes"][0]["before"] = "원고에 없는 이전 주장"
    elif mutation == "hidden_after":
        scope["changes"][0]["after"] = "```text\n숨긴 주장\n```"
    elif mutation == "change_extra":
        scope["changes"][0]["before_evidence"] = []
    elif mutation == "original_type":
        scope["original_draft"] = []
    with pytest.raises(ResearchContractError, match="review request: correction scope"):
        scoped_request(result, scope, sources)


def test_multiline_visible_claim_keeps_exact_scope_but_matches_canonical_whitespace():
    result, scope, sources = corrected(True, "첫 사건의\n기록이 있다.")
    request = scoped_request(result, scope, sources)
    assert request.inputs["correction_scope"]["changes"][0]["after"] == "첫 사건의\n기록이 있다."
    assert "첫 사건의 기록이 있다." in request.inputs["visible_text"]


def test_scope_input_is_not_silently_trimmed_to_fit_budget():
    result, scope, sources = corrected()
    scope["original_draft"] += "가" * 100000
    try:
        with pytest.raises(ResearchContractError, match="input_too_large"):
            scoped_request(result, scope, sources)
    except TypeError as exc:
        pytest.fail(f"approved scoped review boundary missing: {exc}")
