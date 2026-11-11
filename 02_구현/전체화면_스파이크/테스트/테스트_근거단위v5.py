"""v5 총 인용 관계 상한과 독립 canonical 기대값. 네트워크 없음."""
import json
from unittest.mock import patch

import pytest

from 서버.교안연구 import ResearchContractError
from 서버.보고서근거 import build_draft_request, materialize_section


def fixture():
    sources = {"A": "첫 자료에는 독립 기록이 있다.", "B": "두 번째 자료에도 별도 기록이 있다.", "C": "세 번째 자료에도 별도 기록이 있다.", "D": "네 번째 자료에도 별도 기록이 있다."}
    return build_draft_request({"title": "기록"}, [], sources), sources


def primary(text="기록이다.", ref=1):
    return {"내용": text, "근거구간": ref}


def support(ref=2):
    return {"내용": None, "근거구간": ref}


def raw(request, units, enough=True, deficit=""):
    return json.dumps({"근거목록ID": request.inputs["evidence_set_id"], "문장": units, "충분": enough, "부족": deficit})


def test_native_schema_blocks_original_19_reference_shape():
    request, sources = fixture()
    # 실제 실패의 11문장별 관계 수. 자르지 않은 19단위 그대로 검사한다.
    counts = [1, 1, 1, 1, 1, 1, 2, 2, 3, 2, 4]
    units = []
    for number, count in enumerate(counts, 1):
        units.append(primary(f"기록 {number}이다."))
        units.extend(support(ref) for ref in range(2, count + 1))
    assert len(units) == 19
    schema = request.response_schema["properties"]["문장"]
    assert schema["maxItems"] == 16
    assert len(units) > schema["maxItems"]
    assert schema["items"]["properties"]["내용"] == {"type": ["string", "null"]}
    with patch("서버.보고서근거.validate_section") as gate:
        with pytest.raises(ResearchContractError, match="span response: reference limit"):
            materialize_section(raw(request, units), request, sources)
        gate.assert_not_called()


def test_contiguous_supports_bind_most_recent_statement_and_grouping_stable():
    request, sources = fixture()
    result = materialize_section(raw(request, [primary("**첫 주장**이다.", 2), support(1), support(3), primary("둘째 주장이다.", 3), support(4)]), request, sources)
    assert result == {"본문": "첫 주장이다\\. «B» «A» «C»\n\n둘째 주장이다\\. «C» «D»", "충분": True, "부족": "", "근거": [
        {"주장": "첫 주장이다.", "출처": "B", "인용문": sources["B"]},
        {"주장": "첫 주장이다.", "출처": "A", "인용문": sources["A"]},
        {"주장": "첫 주장이다.", "출처": "C", "인용문": sources["C"]},
        {"주장": "둘째 주장이다.", "출처": "C", "인용문": sources["C"]},
        {"주장": "둘째 주장이다.", "출처": "D", "인용문": sources["D"]}]}
    assert request.inputs["draft_protocol"] == "sentence-cited-units-v5"


@pytest.mark.parametrize("units,reason", [
    ([support()], "statement reference"),
    ([{**primary(), "문장번호": 2}], "schema"),
    ([{**primary(), "문장번호": True}], "schema"),
    ([primary(ref=True)], "schema"),
    ([primary(ref="1")], "schema"),
    ([primary(), support(1)], "duplicate reference"),
    ([{"내용": "기록이다.", "문장번호": None}], "schema"),
    ([{"내용": "기록이다.", "근거구간": None}], "schema"),
    ([{"내용": "기록이다.", "문장번호": 1, "근거구간": 1}], "schema"),
    ([{"내용": "기록이다.", "근거구간": [1]}], "schema"),
    ([primary(" ")], "visible content"),
    ([primary("기록 «A»")], "reserved citation"),
    ([primary() for _ in range(17)], "reference limit"),
])
def test_invalid_units_fail_before_canonical_gate(units, reason):
    request, sources = fixture()
    with patch("서버.보고서근거.validate_section") as gate:
        with pytest.raises(ResearchContractError, match="span response: " + reason):
            materialize_section(raw(request, units), request, sources)
        gate.assert_not_called()


def test_sixteen_primary_statements_and_empty_sufficiency():
    request, sources = fixture()
    assert len(materialize_section(raw(request, [primary(f"기록 {n}이다.") for n in range(16)]), request, sources)["근거"]) == 16
    assert materialize_section(raw(request, [], False, "자료 부족"), request, sources) == {"본문": "", "근거": [], "충분": False, "부족": "자료 부족"}
    with pytest.raises(ResearchContractError, match="sufficiency"):
        materialize_section(raw(request, []), request, sources)


def test_internal_encoder_preserves_explicit_relations_without_cutting():
    from 서버 import 보고서근거 as core
    encoder = getattr(core, "encode_citation_units", lambda statements: statements)
    logical = [{"내용": "첫 주장", "근거구간": [2, 1]}, {"내용": "둘째 주장", "근거구간": [3]}]
    assert encoder(logical) == [primary("첫 주장", 2), support(1), primary("둘째 주장", 3)]
    assert logical == [{"내용": "첫 주장", "근거구간": [2, 1]}, {"내용": "둘째 주장", "근거구간": [3]}]
    assert len(encoder([{"내용": str(n), "근거구간": [1]} for n in range(19)])) == 19
