"""§29.11 순수 교정 계약: 실제 모델/저장소를 사용하지 않는다."""

import copy
import json

import pytest

from 서버.교안연구 import ResearchContractError
from 서버.보고서보완 import apply_correction, prepare_correction
from 서버.보고서품질 import plain_markdown


def fixture():
    sources = {"자료&A_[1]": "첫 사건이 기록되어 있다. 이후 별개 사건이 기록되어 있다."}
    claims = ["첫 사건이 다음 사건을 일으켰다.", "다음 사건은 반드시 필연이었다."]
    sections = []
    for i, claim in enumerate(claims, 1):
        sections.append(
            {
                "section_id": f"section-{i}",
                "title": f"절{i}",
                "충분": True,
                "부족": "",
                "본문": "유지할 앞 문장.\n"
                + plain_markdown(claim)
                + " «자료&amp;A\\_\\[1\\]»\n유지할 끝 문장.",
                "근거": [
                    {"주장": claim, "출처": "자료&A_[1]", "인용문": "첫 사건이 기록되어 있다."}
                ],
                "읽은문서": list(sources),
                "메모": [{"기록": "보존"}],
                "order": i,
            }
        )
    draft = (
        "# 보고서\n\n머리말 보존\n\n"
        + "\n\n".join(s["본문"] for s in sections)
        + "\n\n맺음말 보존\n| 표 | 유지 |\n```svg\n<svg/>\n```"
    )
    problems = [
        {"유형": "인과", "설명": "원문보다 강한 추론", "본문발췌": c, "출처": list(sources)}
        for c in claims
    ]
    return draft, sections, problems, sources


def test_shared_claim_and_detached_copies():
    draft, sections, problems, sources = fixture()
    problems = [problems[0], {**problems[0], "본문발췌": "다음 사건을", "유형": "추론"}]
    sections[0]["근거"].append(
        {**sections[0]["근거"][0], "인용문": "이후 별개 사건이 기록되어 있다."}
    )
    request = prepare_correction(draft, sections, problems, sources)
    inputs = request.inputs
    assert len(inputs) == 1 and len(inputs[0]["문제"]) == 2
    assert set(inputs[0]) == {"대상번호", "기존주장", "문제", "기존인용구"}
    schema = request.response_schema
    assert schema["additionalProperties"] is False
    array = schema["properties"]["교정"]
    assert array["minItems"] == array["maxItems"] == 1
    assert array["items"]["additionalProperties"] is False
    assert array["items"]["properties"]["대상번호"] == {"type": "integer", "enum": [1]}
    inputs[0]["기존인용구"].clear()
    schema.clear()
    sections[0]["본문"] = "외부 변조"
    problems[0]["설명"] = "외부 변조"
    result = apply_correction(encode([(1, "**첫 사건이 기록되어 있다.**")]), request, sources)
    assert result["changes"][0]["after"] == "첫 사건이 기록되어 있다."
    assert [e["주장"] for e in result["sections"][0]["근거"]] == ["첫 사건이 기록되어 있다."] * 2
    assert len(request.inputs[0]["기존인용구"]) == 2
    assert "외부 변조" not in request.prompts[1]


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("zero", "problem count"),
        ("three", "problem count"),
        ("unknown", "ambiguous excerpt"),
        ("duplicate", "ambiguous anchor"),
        ("body_duplicate", "ambiguous anchor"),
        ("different_body", "section location"),
        ("hidden", "claim location"),
        ("table", "claim location"),
        ("bad_quote", "quote not in read source"),
        ("unread", "read sources"),
        ("empty_excerpt", "problem fields"),
        ("duplicate_id", "section identity"),
    ],
)
def test_preflight_rejects_before_request(mutation, reason):
    draft, sections, problems, sources = fixture()
    if mutation == "zero":
        problems = []
    elif mutation == "three":
        problems = problems + [problems[0]]
    elif mutation == "unknown":
        problems[0]["본문발췌"] = "없는 주장"
    elif mutation == "duplicate":
        draft += "\n" + plain_markdown(problems[0]["본문발췌"])
    elif mutation == "body_duplicate":
        sections[0]["본문"] += "\n" + plain_markdown(problems[0]["본문발췌"])
    elif mutation == "different_body":
        draft = draft.replace("유지할 앞 문장.", "변경된 앞 문장.")
    elif mutation in {"hidden", "table"}:
        old = sections[0]["본문"]
        anchor = plain_markdown(problems[0]["본문발췌"])
        sections[0]["본문"] = (
            ("```\n" + anchor + "\n```")
            if mutation == "hidden"
            else "| 제목 |\n| --- |\n| " + anchor + " |"
        )
        draft = draft.replace(old, sections[0]["본문"])
    elif mutation == "bad_quote":
        sections[0]["근거"][0]["인용문"] = "원문에 전혀 존재하지 않는 인용구"
    elif mutation == "unread":
        sections[0]["읽은문서"] = ["없는 자료"]
    elif mutation == "empty_excerpt":
        problems[0]["본문발췌"] = " "
    elif mutation == "duplicate_id":
        sections[1]["section_id"] = sections[0]["section_id"]
    with pytest.raises(ResearchContractError, match=reason):
        prepare_correction(draft, sections, problems, sources)


@pytest.mark.parametrize("change", ["modify", "missing", "extra"])
def test_source_version_binding(change):
    draft, sections, problems, sources = fixture()
    request = prepare_correction(draft, sections, problems, sources)
    if change == "modify":
        sources[next(iter(sources))] += "원문 변경"
    elif change == "missing":
        sources.clear()
    else:
        sources["추가"] = "새 자료"
    with pytest.raises(ResearchContractError, match="source version changed"):
        apply_correction(
            encode([(1, "첫 사건이 기록되어 있다."), (2, "다음 사건이 기록되어 있다.")]),
            request,
            sources,
        )


def test_literal_markdown_claim_and_actual_assembly():
    from 서버.교안연구 import Corpus, assemble_report

    draft, sections, problems, sources = fixture()
    claim = "A_[1] & B는 *원인*이다."
    sections[0]["근거"][0]["주장"] = claim
    sections[0]["본문"] = plain_markdown(claim) + " «자료&amp;A\\_\\[1\\]»"
    for section in sections:
        section["role"] = "원인 담당"
    problems = [{**problems[0], "본문발췌": "*원인*이다."}]
    corpus = Corpus.from_data({"docs": sources, "links": {}})
    draft = assemble_report("제목", "머리말", "맺음말", sections, corpus)
    request = prepare_correction(draft, sections, problems, sources)
    result = apply_correction(encode([(1, "A_[1] & B는 **별개**다.")]), request, sources)
    assert result["changes"][0]["after"] == "A_[1] & B는 별개다."
    assert "A\\_\\[1\\] &amp; B는 별개다\\." in result["draft"]
    assert result["sections"][0]["근거"][0]["인용문"] == "첫 사건이 기록되어 있다."


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "null",
        1,
        None,
        '{"교정":[],"교정":[]}',
        '{"교정":[{"대상번호":1,"대상번호":2,"내용":"문장"}]}',
    ],
)
def test_malformed_or_duplicate_json(raw):
    draft, sections, problems, sources = fixture()
    request = prepare_correction(draft, sections, problems, sources)
    with pytest.raises(ResearchContractError, match="invalid response"):
        apply_correction(raw, request, sources)


@pytest.mark.parametrize("slot,value", [(0, None), (1, []), (1, [{}]), (2, None), (3, None)])
def test_invalid_input_shapes(slot, value):
    args = list(fixture())
    args[slot] = value
    with pytest.raises(ResearchContractError):
        prepare_correction(*args)


@pytest.mark.parametrize(
    "mutation", ["missing_evidence", "bad_evidence", "missing_body", "nontext_claim"]
)
def test_invalid_section_shape_is_contract_error(mutation):
    draft, sections, problems, sources = fixture()
    if mutation == "missing_evidence":
        del sections[0]["근거"]
    elif mutation == "bad_evidence":
        sections[0]["근거"] = [None]
    elif mutation == "missing_body":
        del sections[0]["본문"]
    else:
        sections[0]["근거"][0]["주장"] = 42
    with pytest.raises(ResearchContractError, match="section fields"):
        prepare_correction(draft, sections, problems, sources)


def test_no_custom_callbacks_on_source_inputs():
    class Hostile(dict):
        def items(self):
            pytest.fail("custom callback executed")

    draft, sections, problems, sources = fixture()
    with pytest.raises(ResearchContractError, match="invalid inputs"):
        prepare_correction(draft, sections, problems, Hostile(sources))
    request = prepare_correction(draft, sections, problems, sources)
    with pytest.raises(ResearchContractError, match="source version changed"):
        apply_correction("{}", request, Hostile(sources))


def test_two_targets_same_section_do_not_cascade_claim_updates():
    draft, sections, problems, sources = fixture()
    sections[0]["본문"] += "\n\n" + sections[1]["본문"]
    sections[0]["근거"] += sections[1]["근거"]
    sections = sections[:1]
    draft = "# 제목\n\n" + sections[0]["본문"]
    request = prepare_correction(draft, sections, problems, sources)
    result = apply_correction(
        encode([(1, "다음 사건은 반드시 필연이었다."), (2, "다음 사건도 기록되어 있다.")]),
        request,
        sources,
    )
    assert [item["주장"] for item in result["sections"][0]["근거"]] == [
        "다음 사건은 반드시 필연이었다.",
        "다음 사건도 기록되어 있다.",
    ]


def encode(items):
    return json.dumps(
        {"교정": [{"대상번호": i, "내용": text} for i, text in items]}, ensure_ascii=False
    )


def test_two_sections_only_target_splices_and_evidence_change():
    draft, sections, problems, sources = fixture()
    original = copy.deepcopy(sections)
    request = prepare_correction(draft, sections, problems, sources)
    assert request is not None
    result = apply_correction(
        encode([(2, "다음 사건도 기록되어 있다."), (1, "첫 사건이 기록되어 있다.")]),
        request,
        sources,
    )
    expected = copy.deepcopy(original)
    expected_draft = draft
    for i, after in enumerate(["첫 사건이 기록되어 있다.", "다음 사건도 기록되어 있다."]):
        before = original[i]["근거"][0]["주장"]
        expected[i]["근거"][0]["주장"] = after
        expected[i]["본문"] = original[i]["본문"].replace(
            plain_markdown(before), plain_markdown(after)
        )
        expected_draft = expected_draft.replace(plain_markdown(before), plain_markdown(after))
    assert result == {
        "draft": expected_draft,
        "sections": expected,
        "changes": [
            {
                "target_id": i + 1,
                "section_id": f"section-{i + 1}",
                "before": original[i]["근거"][0]["주장"],
                "after": expected[i]["근거"][0]["주장"],
            }
            for i in range(2)
        ],
    }
    assert sections == original


@pytest.mark.parametrize(
    "bad",
    [
        {"교정": [{"대상번호": True, "내용": "새 문장"}, {"대상번호": 2, "내용": "새 문장"}]},
        {"교정": [{"대상번호": 1.0, "내용": "새 문장"}, {"대상번호": 2, "내용": "새 문장"}]},
        {"교정": [{"대상번호": "1", "내용": "새 문장"}, {"대상번호": 2, "내용": "새 문장"}]},
        {"교정": [{"대상번호": 1, "내용": "새 문장"}]},
        {"교정": [{"대상번호": 1, "내용": "새 문장"}, {"대상번호": 1, "내용": "다른 문장"}]},
        {"교정": [{"대상번호": 9, "내용": "새 문장"}, {"대상번호": 2, "내용": "새 문장"}]},
        {"교정": [], "추가": 1},
        {"교정": "문장"},
        [],
        {"교정": [{"대상번호": 1, "내용": 5}, {"대상번호": 2, "내용": "새 문장"}]},
        {
            "교정": [
                {"대상번호": 1, "내용": "새 문장", "출처": "새출처"},
                {"대상번호": 2, "내용": "새 문장"},
            ]
        },
    ],
)
def test_strict_response_rejected_without_mutation(bad):
    draft, sections, problems, sources = fixture()
    request = prepare_correction(draft, sections, problems, sources)
    snapshot = request.inputs
    with pytest.raises(ResearchContractError, match="correction: invalid response"):
        apply_correction(json.dumps(bad), request, sources)
    assert request.inputs == snapshot


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        "```\n숨김\n```",
        "[숨김]: https://example.invalid",
        "«추가출처» 문장",
        "&laquo;추가출처&raquo; 문장",
        "첫 사건이 다음 사건을 일으켰다.",
        "**첫 사건이 다음 사건을 일으켰다.**",
    ],
)
def test_invalid_or_unchanged_visible_replacement(text):
    draft, sections, problems, sources = fixture()
    request = prepare_correction(draft, sections, problems, sources)
    with pytest.raises(ResearchContractError, match="correction: invalid replacement"):
        apply_correction(encode([(1, text), (2, "다음 사건이 기록되어 있다.")]), request, sources)
