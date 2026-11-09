"""순수 계약과 실제 생성 경계의 회귀. 의미 판별 능력을 시험하지 않는다."""

import json

import pytest

from 서버.교안연구 import ResearchContractError, parse_section, section_prompt
from 서버.보고서품질 import (
    parse_semantic,
    parse_synthesis,
    validate_section,
    visible_markdown,
    visual_markdown,
)


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


def test_실제관측한_근거주장_재서술은_본문복사_절차를_요구한다():
    # 실제 Luna 1차는 같은 사실을 재서술해 근거.주장이 본문과 달라졌다.
    # 이 대역은 명시한 복사 절차가 전달된 경우에만 형식을 지킨다. 판단 능력 시험은 아니다.
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
    system, _ = section_prompt(
        {"title": "발생 시기", "instruction": "원문에 근거하라", "role": "시간순 담당"},
        [["임오군란", source]],
    )
    if "본문을 먼저 완성" in system and "다시 요약하지" in system:
        reply["근거"][0]["주장"] = sentence
    actual = validate_section(dump(reply), {"임오군란": source})
    assert actual["근거"][0]["주장"] == sentence


def test_떨어진_원문구간은_하나의인용문으로_합치지않고_근거를_분리한다():
    first = "1882년 8월 30일 청나라군은 민란을 진압하고 한성부 곳곳에 주둔했다."
    last = "일본 제국과 조선은 1882년 8월 30일 제물포 조약을 체결했다."
    source = (
        first + " 임오군란 이후 일본 제국과 청나라는 조선 정부와 불평등 조약을 체결했다. " + last
    )
    claim = first + " " + last
    item = {"주장": claim, "출처": "임오군란", "인용문": claim}
    reply = {"본문": claim + " «임오군란»", "충분": True, "부족": "", "근거": [item]}
    system, _ = section_prompt(
        {"title": "경과", "instruction": "원문에 근거하라", "role": "시간순 담당"},
        [["임오군란", source]],
    )
    # 실제2차의 비연속 인용을 기본 행동으로 삼는 명시 대역이다.
    if "떨어진 원문" in system and "여러 근거 항목" in system:
        reply["근거"] = [{**item, "인용문": quote} for quote in (first, last)]
    actual = validate_section(dump(reply), {"임오군란": source})
    assert [e["인용문"] for e in actual["근거"]] == [first, last]


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
