"""합성 원문으로 검증; 외부 제공자/네트워크/운영 자료 쓰기 없음."""

import copy
import hashlib
import json

import pytest

from 서버.검토문맥 import build_review_request, parse_review
from 서버.교안연구 import ResearchContractError

QUOTE = "처음 사건의 기록은 이곳에 남아 있다."
TEXT = QUOTE + "\n\n다른 문단에는 만석보를 파괴했다는 기록이 명시되어 있다.\n\n" + QUOTE
DRAFT = "만석보를 파괴했다. [보이는 링크](https://hidden.invalid/비밀날짜)"
SECTIONS = [
    {
        "section_id": "s1",
        "읽은문서": ["자료"],
        "본문": DRAFT,
        "근거": [{"주장": "만석보를 파괴했다.", "출처": "자료", "인용문": QUOTE}],
    }
]
SOURCES = {"s1": {"자료": TEXT}}


def payload(request, verdict="supported", problems=None):
    return json.dumps(
        {
            "검토목록ID": request.inputs["review_set_id"],
            "판정": verdict,
            "문제": problems or [],
            "검토요약": "명시적 대역 검토",
        },
        ensure_ascii=False,
    )


def test_원문중복위치_명시와_문맥_분리사본():
    request = build_review_request(DRAFT, SECTIONS, SOURCES)
    contexts = request.inputs["contexts"]
    quotes = [c for c in contexts if c["kind"] == "quote"]
    assert [(c["start"], c["end"], c["text"]) for c in quotes] == [
        (0, len(QUOTE), QUOTE),
        (TEXT.rfind(QUOTE), len(TEXT), QUOTE),
    ]
    assert any("만석보" in c["text"] and c["kind"] != "quote" for c in contexts)
    for c in contexts:
        assert c["source_hash"] == hashlib.sha256(TEXT.encode()).hexdigest()
        assert TEXT[c["start"] : c["end"]] == c["text"]
    detached = request.inputs
    detached["contexts"].clear()
    assert request.inputs["contexts"]
    schema = request.response_schema
    schema.clear()
    assert request.response_schema["additionalProperties"] is False


def test_판정과_근거결합_새계약():
    request = build_review_request(DRAFT, SECTIONS, SOURCES)
    decision = parse_review(payload(request), request, SOURCES)
    assert decision == {
        "통과": True,
        "문제": [],
        "검토요약": "명시적 대역 검토",
        "검토계약": "source-context-review-v1",
        "검토목록ID": request.inputs["review_set_id"],
        "판정": "supported",
        "제공범위": request.inputs["scope"],
    }
    ref = next(c for c in request.inputs["contexts"] if c["kind"] != "quote")
    problem = {
        "유형": "citation_gap",
        "설명": "선택 인용에 누락",
        "본문발췌": "만석보를 파괴했다.",
        "근거번호": [ref["id"]],
    }
    decision = parse_review(payload(request, "needs_revision", [problem]), request, SOURCES)
    assert decision["통과"] is False
    assert decision["문제"][0]["출처"] == ["자료"]
    assert decision["문제"][0]["근거문맥"] == [ref]


@pytest.mark.parametrize(
    "mutation",
    [
        "legacy",
        "duplicate",
        "unknown",
        "bool",
        "verdict",
        "hidden",
        "gap_empty",
        "conflict_one",
        "stale",
    ],
)
def test_엄격한_거절(mutation):
    request = build_review_request(DRAFT, SECTIONS, SOURCES)
    ref = request.inputs["contexts"][0]["id"]
    obj = json.loads(
        payload(
            request,
            "needs_revision",
            [
                {
                    "유형": "contradiction",
                    "설명": "명시 반례",
                    "본문발췌": "만석보를 파괴했다.",
                    "근거번호": [ref],
                }
            ],
        )
    )
    sources = copy.deepcopy(SOURCES)
    if mutation == "legacy":
        obj = {"통과": True, "문제": [], "검토요약": "옛 계약"}
    elif mutation == "unknown":
        obj["문제"][0]["근거번호"] = [999999]
    elif mutation == "bool":
        obj["문제"][0]["근거번호"] = [True]
    elif mutation == "verdict":
        obj["판정"] = "supported"
    elif mutation == "hidden":
        obj["문제"][0]["본문발췌"] = "비밀날짜"
    elif mutation == "gap_empty":
        obj["문제"][0]["유형"] = "citation_gap"
        obj["문제"][0]["근거번호"] = []
    elif mutation == "conflict_one":
        obj["문제"][0]["유형"] = "source_conflict"
        obj["판정"] = "undetermined"
    elif mutation == "stale":
        sources["s1"]["자료"] += "변조"
    raw = json.dumps(obj, ensure_ascii=False)
    if mutation == "duplicate":
        raw = raw[:-1] + ',"판정":"needs_revision"}'
    with pytest.raises(ResearchContractError, match="review"):
        parse_review(raw, request, sources)


def test_다른주장의_선택인용도_누락근거를_가리키는_유효부정일_수_있다():
    first = "갑은 1894년에 마을을 방문했다."
    second = "을은 1895년에 도시로 이동했다."
    claim = "갑은 1894년에 방문했고 을은 1895년에 이동했다."
    text = first + "\n\n" + second
    draft = claim + "\n\n" + second
    sections = [
        {
            "section_id": "s1",
            "읽은문서": ["자료"],
            "본문": draft,
            "근거": [
                {"주장": claim, "출처": "자료", "인용문": first},
                {"주장": second, "출처": "자료", "인용문": second},
            ],
        }
    ]
    sources = {"s1": {"자료": text}}
    request = build_review_request(draft, sections, sources)
    refs = [c["id"] for c in request.inputs["contexts"] if c["kind"] == "quote"]
    assert len(refs) == 2
    problem = {
        "유형": "citation_gap",
        "설명": "첫 주장에 둘째 인용 관계가 빠짐",
        "본문발췌": claim,
        "근거번호": refs,
    }
    result = parse_review(payload(request, "needs_revision", [problem]), request, sources)
    assert result["통과"] is False and result["판정"] == "needs_revision"
    assert result["문제"][0]["유형"] == "citation_gap"


def test_검토지침만_달라도_실제프롬프트와_요청ID가_함께_달라진다(monkeypatch):
    from 서버 import 검토문맥

    before = build_review_request(DRAFT, SECTIONS, SOURCES)
    monkeypatch.setattr(검토문맥, "REVIEW_SYSTEM_RULES", "새로운 독립 검토 지침", raising=False)
    after = build_review_request(DRAFT, SECTIONS, SOURCES)
    assert before.prompts[0] != after.prompts[0]
    assert before.inputs["review_set_id"] != after.inputs["review_set_id"]


def test_추가문맥_예산과_전체원문_미제공_표시():
    text = (
        QUOTE
        + "\n\n"
        + "\n\n".join(
            f"{i}번 기록에서 서로 다른 사건과 주장을 다룬다. " + "한글문맥" * 65 for i in range(600)
        )
    )
    request = build_review_request(DRAFT, SECTIONS, {"s1": {"자료": text}})
    inputs = request.inputs
    additional = [c for c in inputs["contexts"] if c["kind"] != "quote"]
    assert len(additional) <= 192
    assert len(json.dumps(additional, ensure_ascii=False).encode()) <= 96000
    assert inputs["scope"]["additional_bytes"] == len(
        json.dumps(additional, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    )
    assert all(
        len(c["text"]) <= 1600 and text[c["start"] : c["end"]] == c["text"] for c in additional
    )
    assert inputs["scope"]["omitted"] > 0 and inputs["scope"]["incomplete"] is True
    assert inputs["draft"] == DRAFT and inputs["evidence"][0]["근거"] == SECTIONS[0]["근거"]
    assert (
        sum(len(p.encode()) for p in request.prompts)
        + len(json.dumps(request.response_schema, ensure_ascii=False).encode())
        + 8192
        <= 272000
    )


@pytest.mark.parametrize(
    "reason,verdict",
    [
        ("contradiction", "needs_revision"),
        ("unsupported", "needs_revision"),
        ("insufficient_context", "undetermined"),
        ("source_conflict", "undetermined"),
    ],
)
def test_세판정은_문제유형으로_결정하고_자동통과하지_않는다(reason, verdict):
    request = build_review_request(DRAFT, SECTIONS, SOURCES)
    refs = [1] if reason == "contradiction" else [1, 2] if reason == "source_conflict" else []
    problem = {
        "유형": reason,
        "설명": "대역은 의미 증명 아님",
        "본문발췌": "만석보를 파괴했다.",
        "근거번호": refs,
    }
    decision = parse_review(payload(request, verdict, [problem]), request, SOURCES)
    assert decision["통과"] is False and decision["판정"] == verdict


def test_숨은URL_이스케이프와_가시SVG표_발췌_경계():
    draft = '가시 문장 1882년\\. [표제](https://hidden.invalid/1883년)\n\n| 항목 | 연도 |\n| --- | --- |\n| 사건 | 1884년 |\n\n```svg\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 120"><text x="10" y="30">1885년 도식</text></svg>\n```'
    request = build_review_request(draft, SECTIONS, SOURCES)
    for excerpt in ["1882년.", "1884년", "1885년 도식"]:
        problem = {"유형": "unsupported", "설명": "대역", "본문발췌": excerpt, "근거번호": []}
        assert (
            parse_review(payload(request, "needs_revision", [problem]), request, SOURCES)["통과"]
            is False
        )
    for excerpt in ["1883년", "1882년\\."]:
        problem = {"유형": "unsupported", "설명": "대역", "본문발췌": excerpt, "근거번호": []}
        with pytest.raises(ResearchContractError, match="excerpt not visible"):
            parse_review(payload(request, "needs_revision", [problem]), request, SOURCES)


def test_미읽은절_원문과_큰원고_조용히자르지않음():
    with pytest.raises(ResearchContractError, match="section scope"):
        build_review_request(DRAFT, SECTIONS, {"s1": {"자료": TEXT, "미읽기": "금지"}})
    with pytest.raises(ResearchContractError, match="input_too_large"):
        build_review_request("가" * 100000, SECTIONS, SOURCES)
    first = build_review_request(DRAFT, SECTIONS, SOURCES)
    second = build_review_request(DRAFT + "추가", SECTIONS, SOURCES)
    with pytest.raises(ResearchContractError, match="request mismatch"):
        parse_review(payload(first), second, SOURCES)


def test_가시SVG는_투명글자와_상속된숨김을_포함하지않는다():
    draft = """```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 120"><text x="10" y="30">보이는 글자</text><g opacity="0"><text x="10" y="50">숨긴 날짜</text></g><text fill="none" x="10" y="70">그리지않는 날짜</text></svg>
```"""
    request = build_review_request(draft, SECTIONS, SOURCES)
    assert "보이는 글자" in request.inputs["visible_text"]
    assert "숨긴 날짜" not in request.inputs["visible_text"]
    assert "그리지않는 날짜" not in request.inputs["visible_text"]


def test_원래_중복인용_위치는_잘라통과시키지않고_입력상한차단():
    repeated = "공개 자료 사실 및 범위. " * 3000
    sections = copy.deepcopy(SECTIONS)
    sections[0]["근거"][0]["인용문"] = repeated[:320]
    with pytest.raises(ResearchContractError, match="input_too_large"):
        build_review_request(DRAFT, sections, {"s1": {"자료": repeated}})


@pytest.mark.parametrize(
    "claim,reason,needle",
    [
        ("농민군은 만석보를 파괴하고 고부 관아로 갔다.", "citation_gap", "만석보를 파괴하고"),
        ("정부는 강화 조건을 이행하지 않았다.", "citation_gap", "정부는 강화 조건을 이행하지 않는"),
        ("백산·금구·삼례는 같은 시점에 같은 구호로 봉기했다.", "unsupported", None),
        ("김옥균 피살이 1884년 갑신정변을 직접 일으켰다.", "contradiction", "1894년 3월 28일"),
    ],
)
def test_보존코퍼스의_문맥발견은_선택인용이나_복합주장_자동통과가_아니다(claim, reason, needle):
    from pathlib import Path

    corpus = Path(__file__).resolve().parents[3] / "01_참고자료/원본데이터/corpus.json"
    docs = json.loads(corpus.read_text(encoding="utf-8"))["docs"]
    key = "김옥균" if "김옥균" in claim else "동학 농민 혁명"
    text = docs[key]
    # 실제 원문의 무관한 10~400자 선택 인용은 바꾸지 않는다.
    quote = text[100:250]
    sections = [
        {
            "section_id": "s1",
            "읽은문서": [key],
            "본문": claim,
            "근거": [{"주장": claim, "출처": key, "인용문": quote}],
        }
    ]
    request = build_review_request(claim, sections, {"s1": {key: text}})
    offered = request.inputs["contexts"]
    refs = (
        [next(c["id"] for c in offered if c["kind"] != "quote" and needle in c["text"])]
        if needle
        else []
    )
    problem = {
        "유형": reason,
        "설명": "명시 대역; 모델 판단 성능의 증거 아님",
        "본문발췌": claim,
        "근거번호": refs,
    }
    decision = parse_review(
        payload(request, "needs_revision", [problem]), request, {"s1": {key: text}}
    )
    assert decision["통과"] is False
    assert request.inputs["evidence"][0]["근거"][0]["인용문"] == quote
    assert decision["문제"][0]["유형"] == reason
