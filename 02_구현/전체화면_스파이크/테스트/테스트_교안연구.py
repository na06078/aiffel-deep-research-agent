"""고정 코퍼스 실제 함수 검사. 모델/역사적 의미 검증과 구별한다."""

from pathlib import Path

import pytest

from 서버.교안연구 import Corpus, ResearchContractError, evaluate_report, parse_plan, parse_section

CORPUS = Path(__file__).resolve().parents[3] / "01_참고자료/원본데이터/corpus.json"


def test_카드는_앞250자이며_실제_코퍼스_참조를_검사한다():
    corpus = Corpus(CORPUS)
    assert len(corpus.docs) == 34
    title = next(iter(corpus.docs))
    assert (
        corpus.cards().splitlines()[0]
        == f"- {title}: {corpus.docs[title][:250].replace(chr(10), ' ')}"
    )
    assert all(target in corpus.docs for links in corpus.links.values() for target in links)
    with pytest.raises(ResearchContractError, match="corpus"):
        Corpus.from_data({"docs": {"A": "원문"}, "links": {"A": ["없는문서"]}})


def test_기획은_실제_분리씨앗과_안정식별자_원고는_정확한bool만_허용한다():
    import json

    c = Corpus(CORPUS)
    titles = list(c.docs)
    obj = {
        "제목": "연구",
        "목차": [
            {"절": "동학농민운동", "지시": "원인", "역할": "원인 담당", "시작문서": titles[0]},
            {"절": "갑오개혁", "지시": "원인", "역할": "시간순 담당", "시작문서": titles[1]},
        ],
    }
    title, plan = parse_plan(json.dumps(obj), c)
    assert title == "연구"
    assert [(a["section_id"], a["assignee"], a["seed"]) for a in plan] == [
        ("section-1", "researcher-1", titles[0]),
        ("section-2", "researcher-2", titles[1]),
    ]
    obj["목차"][1]["시작문서"] = titles[0]
    with pytest.raises(ResearchContractError, match="plan"):
        parse_plan(json.dumps(obj), c)
    assert (
        parse_section('{"본문":"보존 원고", "충분":false, "부족":"원인 근거 없음"}')["충분"]
        is False
    )
    for raw in [
        '{"본문":"본문", "충분":"false", "부족":""}',
        '{"본문":"본문", "충분":false, "부족":""}',
        "not-json",
    ]:
        with pytest.raises(ResearchContractError, match="section"):
            parse_section(raw)


def test_평가는_없는문서_미독문서_타절인용을_차단하고_경고는_허용한다():
    c = Corpus.from_data({"docs": {x: x for x in "ABC"}, "links": {}})
    sections = [
        {"section_id": "section-1", "본문": "원고 «A» «B» «C» «없는문서»", "읽은문서": ["A"]},
        {"section_id": "section-2", "본문": "원고 «B»", "읽은문서": ["B"]},
    ]
    quality = evaluate_report("# 제목\n" + "본문" * 1100 + " «A» «B» «C» «없는문서»", sections, c)
    assert not quality["passed"]
    assert quality["unknown_citations"] == ["없는문서"]
    assert quality["unread_citations"] == ["C"]
    assert quality["misattributed_citations"] == [
        {"section_id": "section-1", "documents": ["B", "C", "없는문서"]}
    ]
    good = [{"section_id": "section-1", "본문": "근거 문장입니다. «A» " * 200, "읽은문서": ["A"]}]
    quality = evaluate_report("# 제목\n" + good[0]["본문"], good, c)
    assert quality["passed"]
    assert quality["metrics"]["인용편중"] == 100
    assert quality["metrics"]["읽은문서"] == 1
    assert quality["warnings"]


def test_출처목록으로_본문2000자를_부풀릴수_없다():
    c = Corpus.from_data({"docs": {"A": "원문"}, "links": {}})
    section = {"section_id": "section-1", "본문": "짧은 본문 «A»", "읽은문서": ["A"]}
    q = evaluate_report(
        "# 제목\n짧은 본문 «A»\n\n## 출처 및 이용허락\n" + "출처" * 2000, [section], c
    )
    assert q["passed"] is False
    assert "body_under_2000" in q["reasons"]


def test_링크후보만_선택하고_타절구역과_중복을_피한다():
    c = Corpus.from_data({"docs": {x: x for x in "ABCDE"}, "links": {"A": ["B", "C"]}})
    assert c.candidates(["A"], ["B"]) == ["C"]
    assert c.pick('{"문서":"C"}', ["C"], ["A"]) == "C"
    for raw in ['{"문서":"D"}', '{"문서":"A"}', '{"문서":3}', "bad json"]:
        with pytest.raises(ResearchContractError, match="selection"):
            c.pick(raw, ["C"], ["A"])
    assert c.pick('{"문서":null}', ["C"], ["A"]) is None
    assert c.candidates(["A", "C"], ["B", "D", "E"]) == ["B", "D", "E"]
