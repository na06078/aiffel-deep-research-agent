"""순수 계약과 실제 생성 경계의 회귀. 의미 판별 능력을 시험하지 않는다."""

import json

import pytest

from 서버.교안연구 import ResearchContractError, parse_section
from 서버.보고서근거 import build_draft_request, materialize_section
from 서버.보고서품질 import (
    parse_semantic,
    parse_synthesis,
    validate_section,
    visible_markdown,
    visual_markdown,
)
from 테스트.근거단위대역 import citation_units


def dump(value):
    return json.dumps(value, ensure_ascii=False)


def section():
    return {
        "본문": "1894년 7월 23일 사건이 있었다. «자료A»",
        "충분": True,
        "부족": "",
        "근거": [
            {
                "주장": "1894년 7월 23일 사건이 있었다.",
                "출처": "자료A",
                "인용문": "1894년 7월 23일 사건이 있었다.",
            }
        ],
    }


def test_실제관측한_근거주장_재서술은_거절하고_새본문은_한번만_작성한다():
    # 실제 Luna 1차는 같은 사실을 재서술해 근거.주장이 본문과 달라졌다.
    # 구형 반례는 validator에 남기고 신규 계약에서는 코드가 주장을 만든다.
    source = "임오군란은 1882년 7월 23일부터 8월 30일까지 이어진 조선 한성부에서 발발한 구식 군인의 폭동이다."
    sentence = (
        "임오군란은 1882년 7월 23일 조선 한성부에서 발발했으며, 구식 군인의 폭동으로 시작되었다."
    )
    reply = {
        "본문": sentence
        + " «임오군란» 이 폭동은 1882년 7월 23일부터 8월 30일까지 이어졌다. «임오군란»",
        "충분": True,
        "부족": "",
        "근거": [
            {
                "주장": "임오군란은 1882년 7월 23일부터 8월 30일까지 이어졌고, 구식 군인의 폭동으로 시작되었다.",
                "출처": "임오군란",
                "인용문": source,
            }
        ],
    }
    with pytest.raises(ResearchContractError, match="claim not in visible body"):
        validate_section(dump(reply), {"임오군란": source})
    request = build_draft_request(
        {"title": "발생 시기", "instruction": "원문에 근거하라", "role": "시간순 담당"},
        [["임오군란", source]],
        {"임오군란": source},
    )
    offered = json.loads(request.prompts[1])["evidence"]
    raw = dump(
        {
            "근거목록ID": json.loads(request.prompts[1])["evidence_set_id"],
            "문장": citation_units([{"내용": sentence, "근거구간": [offered[0]["id"]]}]),
            "충분": True,
            "부족": "",
        }
    )
    actual = materialize_section(raw, request, {"임오군란": source})
    assert actual["근거"][0]["주장"] == sentence
    assert actual["근거"][0]["인용문"] == source
    assert visible_markdown(actual["본문"]) == sentence + " «임오군란»"


def test_떨어진_원문구간은_하나의인용문으로_합치지않고_근거를_분리한다():
    first = "1882년 8월 30일 청나라군은 민란을 진압하고 한성부 곳곳에 주둔했다."
    last = "일본 제국과 조선은 1882년 8월 30일 제물포 조약을 체결했다."
    # v3: 명시 문단 경계가 320자에 끝난다. 중간 기록을 건너뛴 가짜 인용 반례는 유지한다.
    first_block = (first + " 중간 기록" * 40).ljust(318, "기")[:318] + "\n\n"
    source = first_block + last
    claim = first + " " + last
    item = {"주장": claim, "출처": "임오군란", "인용문": claim}
    reply = {"본문": claim + " «임오군란»", "충분": True, "부족": "", "근거": [item]}
    with pytest.raises(ResearchContractError, match="quote not in read source"):
        validate_section(dump(reply), {"임오군란": source})
    request = build_draft_request(
        {"title": "경과", "instruction": "원문에 근거하라", "role": "시간순 담당"},
        [["임오군란", source]],
        {"임오군란": source},
    )
    by_start = {e["start"]: e for e in json.loads(request.prompts[1])["evidence"]}
    raw = dump(
        {
            "근거목록ID": json.loads(request.prompts[1])["evidence_set_id"],
            "문장": citation_units([{"내용": claim, "근거구간": [by_start[0]["id"], by_start[320]["id"]]}]),
            "충분": True,
            "부족": "",
        }
    )
    actual = materialize_section(raw, request, {"임오군란": source})
    assert [e["인용문"] for e in actual["근거"]] == [first_block, last]
    assert [e["주장"] for e in actual["근거"]] == [claim, claim]


def test_공백만_정규화하고_날짜와단위는_고치지_않는다():
    value = section()
    raw = dump(value)
    actual = validate_section(raw, {"자료A": "원문: 1894년\n7월 23일 사건이 있었다. 이후 기록."})
    assert actual["근거"] == value["근거"]
    assert actual["본문"] == value["본문"]
    assert "근거" not in parse_section(raw)  # 레거시 파서 호출은 유지한다.


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("other_source", "source not read"),
        ("wrong_date", "quote not in"),
        ("not_in_body", "claim not in"),
        ("too_short", "quote not in"),
        ("too_long", "quote not in"),
        ("too_many", "list required"),
        ("missing", "list required"),
        ("empty", "list required"),
    ],
)
def test_다른문서귀속과_없는인용과_없는주장을_거부한다(mutation, reason):
    value = section()
    if mutation == "other_source":
        value["근거"][0]["출처"] = "자료B"
    elif mutation == "wrong_date":
        value["근거"][0]["인용문"] = "1894년 7월 24일 사건이 있었다."
    elif mutation == "not_in_body":
        value["근거"][0]["주장"] = "날짜가 다르다."
    elif mutation == "too_short":
        value["근거"][0]["인용문"] = "1894년"
    elif mutation == "too_long":
        value["근거"][0]["인용문"] = "기" * 401
    elif mutation == "too_many":
        value["근거"] *= 17
    elif mutation == "missing":
        value.pop("근거")
    else:
        value["근거"] = []
    # 자료B가 전체 코퍼스에 있어도 이 절이 실제 읽지 않았다면 입력 맵에 없다.
    with pytest.raises(ResearchContractError, match=reason):
        validate_section(dump(value), {"자료A": "1894년 7월 23일 사건이 있었다."})


def test_부족초안은_빈근거와_원고를_그대로_보존한다():
    value = {"본문": "자료가 부족합니다.", "충분": False, "부족": "달력과 날짜 미확인", "근거": []}
    assert validate_section(dump(value), {}) == value


def test_링크_title에만_숨긴_주장은_가시본문의_근거로_통과하지_않는다():
    value = section()
    value["본문"] = (
        '[자료](https://example.invalid/source "1894년 7월 23일 사건이 있었다.") «자료A»'
    )
    with pytest.raises(ResearchContractError, match="claim not in visible body"):
        validate_section(dump(value), {"자료A": "1894년 7월 23일 사건이 있었다."})


def test_실제_표와_인라인코드와_강조는_보이는_문자로_추출한다():
    markdown = "| 항목 | 값 |\n|---|---|\n| **날짜** | `1894년` «자료\\(A\\)\\.B» |"
    assert visible_markdown(markdown) == "항목\n값\n날짜\n1894년 «자료(A).B»"


def test_강조표시가_있는_가시주장은_원문과_연결할_수_있다():
    value = section()
    value["본문"] = "**1894년** 7월 23일 사건이 있었다. «자료A»"
    actual = validate_section(dump(value), {"자료A": "1894년 7월 23일 사건이 있었다."})
    assert actual["근거"] == value["근거"]


def test_종합계약은_4열5셀_반례를_거절하며_2열6열을_허용한다():
    from 서버.종합계약 import build_synthesis_schema

    schema = build_synthesis_schema(["자료A"])
    assert schema.get("type") == "object", "종합 root schema가 없다"
    branches = schema["properties"]["표"]["items"]["anyOf"]

    def accepts(columns, cells):
        return any(
            b["properties"]["열"]["minItems"] <= len(columns) <= b["properties"]["열"]["maxItems"]
            and b["properties"]["행"]["items"]["properties"]["셀"]["minItems"]
            <= len(cells)
            <= b["properties"]["행"]["items"]["properties"]["셀"]["maxItems"]
            for b in branches
        )

    assert len(branches) == 5
    assert not accepts(["사건", "연도", "원인", "결과"], ["가", "나", "다", "라", "출처"])
    assert accepts(["사건", "연도"], ["가", "나"])
    assert accepts(["가", "나", "다", "라", "마", "바"], ["1", "2", "3", "4", "5", "6"])
    for branch in branches:
        row = branch["properties"]["행"]["items"]
        assert set(row["required"]) == {"셀", "출처"}
        assert row["properties"]["출처"] == {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "enum": ["자료A"]},
        }


@pytest.mark.parametrize("bad", ["자료A", b"A", [""], ["  "], [3], [None]])
def test_종합계약은_출처키를_추정하거나_정규화하지않는다(bad):
    from 서버.종합계약 import build_synthesis_schema

    with pytest.raises(ValueError, match="invalid_source_ids"):
        build_synthesis_schema(bad)


def test_종합계약의_빈출처와_분리사본과_키원형과_닫힌객체():
    from 서버.종합계약 import SYNTHESIS_PROTOCOL, build_synthesis_schema

    assert SYNTHESIS_PROTOCOL == "source-bound-synthesis-v1"
    source_ids = ["자료B", " 자료A ", "자료B"]
    original = list(source_ids)
    schema = build_synthesis_schema(iter(source_ids))
    diagram = schema["properties"]["도식"]
    assert diagram["maxItems"] == 2
    assert diagram["items"]["properties"]["출처"]["items"]["enum"] == [" 자료A ", "자료B"]
    assert source_ids == original
    detached = build_synthesis_schema({"자료B": "ignored", " 자료A ": "ignored"})
    assert detached == schema
    diagram["items"]["properties"]["출처"]["items"]["enum"].append("변조")
    assert detached["properties"]["도식"]["items"]["properties"]["출처"]["items"]["enum"] == [
        " 자료A ",
        "자료B",
    ]
    assert detached["properties"]["표"]["items"]["anyOf"][0]["properties"]["행"]["items"][
        "properties"
    ]["출처"]["items"]["enum"] == [" 자료A ", "자료B"]
    empty = build_synthesis_schema([])
    assert empty["properties"]["표"]["maxItems"] == empty["properties"]["도식"]["maxItems"] == 0
    assert "minItems" not in detached["properties"]["도식"]
    assert set(empty["required"]) == {"머리말", "맺음말", "표", "도식", "시각화생략사유"}

    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "anyOf",
        "minItems",
        "maxItems",
        "items",
        "pattern",
    }

    def walk(node):
        assert type(node) is dict and set(node) <= allowed
        if "pattern" in node:
            assert node["type"] == "string" and type(node["pattern"]) is str
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        for child in node.get("properties", {}).values():
            walk(child)
        if "items" in node:
            walk(node["items"])
        for child in node.get("anyOf", []):
            walk(child)

    walk(detached)
    walk(empty)
    assert json.loads(json.dumps(detached, ensure_ascii=False)) == detached


@pytest.mark.parametrize(
    "mutation",
    ["missing_source", "empty_source", "unknown_source", "four_five", "valid_two", "valid_six"],
)
def test_종합계약의_관측반례는_응답수선없이_제공자제약으로_배제한다(mutation):
    from 서버.종합계약 import build_synthesis_schema

    # 구체적 응답 예시를 schema 구조와 독립적으로 명시한다.
    table = {
        "제목": "비교",
        "열": ["사건", "연도"],
        "행": [{"셀": ["사건A", "1882년"], "출처": ["자료A"]}],
    }
    row = table["행"][0]
    if mutation == "missing_source":
        row.pop("출처")
    elif mutation == "empty_source":
        row["출처"] = []
    elif mutation == "unknown_source":
        row["출처"] = ["미독자료"]
    elif mutation == "four_five":
        table["열"] = ["사건", "연도", "원인", "결과"]
        row["셀"] = ["사건A", "1882년", "원인A", "결과A", "자료A"]
    elif mutation == "valid_six":
        table["열"] = ["가", "나", "다", "라", "마", "바"]
        row["셀"] = ["1", "2", "3", "4", "5", "6"]
    before = dump(table)

    def accepts(node, value):
        # 테스트 전용 JSON Schema 부분집합 해석기. builder의 분기 생성과 공유하지 않는다.
        if "anyOf" in node:
            return any(accepts(child, value) for child in node["anyOf"])
        kind = node["type"]
        if kind == "object":
            return (
                type(value) is dict
                and set(node["required"]) <= set(value)
                and set(value) <= set(node["properties"])
                and all(accepts(node["properties"][key], val) for key, val in value.items())
            )
        if kind == "array":
            return (
                type(value) is list
                and node.get("minItems", 0) <= len(value) <= node.get("maxItems", float("inf"))
                and all(accepts(node["items"], val) for val in value)
            )
        assert kind == "string"
        return type(value) is str and ("enum" not in node or value in node["enum"])

    node = build_synthesis_schema(["자료A"])["properties"]["표"]["items"]
    assert accepts(node, table) is (mutation in {"valid_two", "valid_six"})
    assert dump(table) == before


def synthesis():
    return {
        "머리말": "머리말",
        "맺음말": "맺음말",
        "표": [],
        "도식": [],
        "시각화생략사유": "근거 부족",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "columns",
        "rows",
        "cells",
        "long_cell",
        "tables",
        "diagrams",
        "unknown",
        "omission",
        "malicious_svg",
    ],
)
def test_시각요약의_상한과_출처와SVG를_검사한다(mutation):
    value = synthesis()
    table = {"제목": "표", "열": ["항목", "값"], "행": [{"셀": ["가", "나"], "출처": ["자료A"]}]}
    value["표"] = [table]
    if mutation == "columns":
        table["열"] = ["하나"]
    elif mutation == "rows":
        table["행"] *= 11
    elif mutation == "cells":
        table["행"][0]["셀"] = ["하나"]
    elif mutation == "long_cell":
        table["행"][0]["셀"][0] = "가" * 301
    elif mutation == "tables":
        value["표"] *= 3
    elif mutation == "diagrams":
        value["도식"] = [{}] * 3
    elif mutation == "unknown":
        table["행"][0]["출처"] = ["자료B"]
    elif mutation == "omission":
        value["표"], value["시각화생략사유"] = [], ""
    else:
        value["도식"] = [
            {
                "제목": "악성",
                "설명": "거부",
                "출처": ["자료A"],
                "코드": '<svg viewBox="0 0 10 10"><script>bad()</script></svg>',
            }
        ]
    with pytest.raises(ResearchContractError, match="synthesize"):
        parse_synthesis(dump(value), {"자료A"})


def test_시각자료없는_생략사유는_텍스트이며_원래조립호출도_가능하다():
    value = synthesis()
    assert parse_synthesis(dump(value), set()) == value
    assert visual_markdown(None) == []
    assert visual_markdown(value) == ["시각화 생략 사유: 근거 부족"]


def test_종합프롬프트의_완성예시는_그대로_파싱되는_유효한SVG다():
    import json
    import xml.etree.ElementTree as ET

    from 서버.교안연구 import synthesis_prompt

    system, _ = synthesis_prompt("질문", "제목", [])
    example = json.loads(system.split("JSON으로만: ", 1)[1])
    assert "연표나 병렬 카드" in system
    assert "인과를 뜻하지 않는 SVG" in system
    assert "출처 열은 렌더러가 추가" in system
    assert "열/셀에 출처를 추가하지 마라" in system
    assert len(example["표"]) == len(example["도식"]) == 1
    code = example["도식"][0]["코드"]
    # 원격 응답이 아닌 소스의 작은 고정 예시만 표준 XML 파서로 독립 대조한다.
    svg = ET.fromstring(code)
    assert [float(value) for value in svg.attrib["viewBox"].split()] == [0, 0, 600, 180]
    assert "\\" not in code
    assert parse_synthesis(json.dumps(example, ensure_ascii=False), {"자료 키"})["도식"]


@pytest.mark.parametrize(
    "value",
    [
        {"통과": "true", "문제": [], "검토요약": "요약"},
        {"통과": True, "문제": [{}], "검토요약": "요약"},
        {"통과": False, "문제": [], "검토요약": "요약"},
        {"통과": True, "문제": [], "검토요약": ""},
        {
            "통과": False,
            "문제": [{"유형": "귀속", "설명": "다른 출처", "본문발췌": "본문", "출처": ["자료B"]}],
            "검토요약": "요약",
        },
    ],
)
def test_의미검토는_타입과_모순과_귀속을_검사한다(value):
    with pytest.raises(ResearchContractError, match="semantic_review"):
        parse_semantic(dump(value), {"자료A"})


def test_미지원주장은_출처가_비어있어도_음성판정을_보존한다():
    value = {
        "통과": False,
        "문제": [
            {"유형": "미지원", "설명": "읽은 자료에 없음", "본문발췌": "자료 밖 주장", "출처": []}
        ],
        "검토요약": "보증할 수 없음",
    }
    assert parse_semantic(dump(value), {"자료A"}) == value


@pytest.mark.parametrize("source", ["자료(A).B", "자료&amp;", "자료\\!"])
def test_표의_이스케이프된_출처도_원래식별자로_평가한다(source):
    from 서버.교안연구 import Corpus, assemble_report, evaluate_report

    value = synthesis()
    value["표"] = [
        {"제목": "표", "열": ["항목", "값"], "행": [{"셀": ["사실", "값"], "출처": [source]}]}
    ]
    corpus = Corpus.from_data({"docs": {source: "원문"}, "links": {}})
    sections = [
        {
            "section_id": "section-1",
            "title": "절",
            "role": "큰그림 담당",
            "본문": "가시 본문입니다. " * 200,
            "읽은문서": [source],
        }
    ]
    markdown = assemble_report("제목", "머리말", "맺음말", sections, corpus, value)
    quality = evaluate_report(markdown, sections, corpus)
    assert quality["passed"], quality
    assert quality["unknown_citations"] == []
    assert quality["metrics"]["인용수"] == 1


def test_SVG텍스트의_백틱이_코드펜스를_탈출하지_못한다():
    from markdown_it import MarkdownIt

    value = synthesis()
    value["도식"] = [
        {
            "제목": "그림",
            "설명": "대역",
            "출처": ["자료A"],
            "코드": '<svg viewBox="0 0 100 100"><text>\n```\n외부 코드 아님\n```\n</text></svg>',
        }
    ]
    parsed = parse_synthesis(dump(value), {"자료A"})
    markdown = "\n\n".join(visual_markdown(parsed))
    fences = [t for t in MarkdownIt("commonmark").parse(markdown) if t.type == "fence"]
    assert len(fences) == 1
    assert fences[0].info == "svg"
    assert "외부 코드 아님" in fences[0].content
