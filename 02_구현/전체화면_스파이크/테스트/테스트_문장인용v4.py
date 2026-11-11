"""승인 v4 wire의 독립 기대값. 합성 자료만, 제공자/파일 IO 없음."""

import json
from unittest.mock import patch

import pytest

from 서버.교안연구 import ResearchContractError
from 서버.보고서근거 import build_draft_request, materialize_section
from 테스트.근거단위대역 import citation_units

TEXT = "첫 자료에는 독립 기록이 있다."


def fixture():
    sources = {"A": TEXT, "B": "두 번째 자료에도 별도 기록이 있다."}
    request = build_draft_request({"title": "기록"}, [], sources)
    return request, sources


def wire(request, sentences=None, enough=True, deficit=""):
    return {
        "근거목록ID": request.inputs["evidence_set_id"],
        "문장": citation_units(sentences if sentences is not None else [{"내용": "**첫 주장**이다.", "근거구간": [2, 1]}]),
        "충분": enough,
        "부족": deficit,
    }


def test_v4_materializes_exact_canonical_words_and_order():
    request, sources = fixture()
    result = materialize_section(json.dumps(wire(request)), request, sources)
    assert result == {
        "본문": "첫 주장이다\\. «B» «A»",
        "충분": True,
        "부족": "",
        "근거": [
            {"주장": "첫 주장이다.", "출처": "B", "인용문": sources["B"]},
            {"주장": "첫 주장이다.", "출처": "A", "인용문": TEXT},
        ],
    }
    assert request.inputs["draft_protocol"] == "sentence-cited-units-v5"


def test_schema_embeds_required_reference_array_and_preserves_budget():
    request, _ = fixture()
    schema = request.response_schema
    assert set(schema["required"]) == {"근거목록ID", "문장", "충분", "부족"}
    sentences = schema["properties"]["문장"]
    assert sentences["minItems"] == 0 and sentences["maxItems"] == 16
    item = sentences["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"내용", "근거구간"}
    assert item["properties"]["내용"] == {"type": ["string", "null"]}
    assert item["properties"]["근거구간"] == {"type": "integer", "enum": [1, 2]}
    assert (
        sum(len(p.encode()) for p in request.prompts) + request.inputs["schema_bytes"] + 8192
        <= 272000
    )
    item["properties"].clear()
    assert request.response_schema["properties"]["문장"]["items"]["properties"]


@pytest.mark.parametrize(
    "refs,reason",
    [
        ([], "schema"),
        ([True], "schema"),
        (["1"], "schema"),
        ([1.0], "schema"),
        ([1, 1], "duplicate reference"),
        ([0], "unoffered ID"),
        ([3], "unoffered ID"),
    ],
)
def test_each_sentence_reference_is_mandatory_even_when_insufficient(refs, reason):
    request, sources = fixture()
    for enough, deficit in [(True, ""), (False, "후속 기록 미확인")]:
        with pytest.raises(ResearchContractError, match="span response: " + reason):
            materialize_section(
                json.dumps(
                    wire(request, [{"내용": "기록이다.", "근거구간": refs}], enough, deficit)
                ),
                request,
                sources,
            )


def test_total_reference_limit_is_sum_not_per_sentence():
    request, sources = fixture()
    sentences = [{"내용": f"주장 {i}.", "근거구간": [1, 2]} for i in range(8)]
    assert (
        len(materialize_section(json.dumps(wire(request, sentences)), request, sources)["근거"])
        == 16
    )
    with patch("서버.보고서근거.validate_section") as canonical:
        with pytest.raises(ResearchContractError, match="reference limit"):
            materialize_section(
                json.dumps(wire(request, sentences + [{"내용": "초과.", "근거구간": [1]}])),
                request,
                sources,
            )
        canonical.assert_not_called()


def test_empty_insufficient_has_explicit_deficit_and_no_invented_claim():
    request, sources = fixture()
    assert materialize_section(
        json.dumps(wire(request, [], False, "자료 부족")), request, sources
    ) == {"본문": "", "충분": False, "부족": "자료 부족", "근거": []}
    with pytest.raises(ResearchContractError, match="sufficiency"):
        materialize_section(json.dumps(wire(request, [])), request, sources)
    with pytest.raises(ResearchContractError, match="sufficiency"):
        materialize_section(json.dumps(wire(request, [], False)), request, sources)


def test_flat_v3_is_not_automatically_repaired():
    request, sources = fixture()
    legacy = {
        **wire(request),
        "문장": ["첫 주장이다."],
        "근거관계": [{"문장번호": 1, "근거구간": 1}],
    }
    with pytest.raises(ResearchContractError, match="span response: schema"):
        materialize_section(json.dumps(legacy), request, sources)


@pytest.mark.parametrize("integration", [False, True])
def test_shared_web_doubles_emit_materializable_v4_without_services(integration):
    import asyncio

    from 테스트.테스트_웹연구 import WebModel
    from 테스트.테스트_웹통합 import IntegrationModel

    sources = {"A": TEXT, "B": TEXT, "C": TEXT}
    request = build_draft_request({"title": "공개 자료"}, [], sources)
    model = IntegrationModel() if integration else WebModel()
    model.excerpts = dict(sources)
    attempts = []
    response = asyncio.run(
        model.complete(
            *request.prompts,
            context={"operation": "draft_section", "actor": "대역"},
            on_attempt=attempts.append,
            response_schema=request.response_schema,
        )
    )
    obj = json.loads(response["content"])
    assert set(obj) == {"근거목록ID", "문장", "충분", "부족"}
    assert obj["문장"][0]["근거구간"] == 1
    actual = materialize_section(response["content"], request, sources)
    assert actual["근거"][0]["인용문"] == TEXT
    assert attempts == ["attempt-1"] and len(model.calls) == 1
