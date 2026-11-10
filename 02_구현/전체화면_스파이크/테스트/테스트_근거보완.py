"""§29.18 순수 경계와 실제 실패 원본의 읽기 전용 준비 검사. 모델 호출 없음."""

import copy
import json

import pytest

from 서버.교안연구 import ResearchContractError
from 서버.근거보완 import apply_evidence_correction, prepare_evidence_correction
from 서버.보고서품질 import plain_markdown


def fixture():
    sources = {"자료&A_[1]": "첫 사건의 기록은 남아 있다. 다음 사건은 별도로 기록되어 있다."}
    claim = "첫 사건의 기록은 남아 있다."
    paragraph = plain_markdown(claim) + " «자료&amp;A\\_\\[1\\]»"
    section = {
        "section_id": "s1",
        "title": "보존 절",
        "order": 1,
        "round": 1,
        "본문": "앞 문단 보존.\n\n" + paragraph + "\n\n끝 문단 보존.",
        "근거": [
            {
                "주장": claim,
                "출처": next(iter(sources)),
                "인용문": "다음 사건은 별도로 기록되어 있다.",
            }
        ],
        "읽은문서": list(sources),
        "충분": True,
        "부족": "",
        "메모": [["보존"]],
    }
    draft = "# 제목\n\n머리말\n\n" + section["본문"] + "\n\n맺음말\n\n| 표 | 보존 |"
    problems = [
        {
            "유형": "citation_gap",
            "설명": "첫 사건 기록의 구간을 인용해야 한다.",
            "본문발췌": claim,
            "출처": list(sources),
        }
    ]
    return draft, [section], problems, sources


def response(request, text="첫 사건의 기록은 남아 있다."):
    catalog = request.inputs["catalog"]
    ref = next(
        e["id"]
        for e in catalog
        if e["text"] == "첫 사건의 기록은 남아 있다. 다음 사건은 별도로 기록되어 있다."
    )
    return {
        "근거목록ID": request.inputs["evidence_set_id"],
        "교정": [{"대상번호": 1, "내용": text, "근거구간": [ref]}],
    }


def test_인용만교체_다른바이트와메타데이터보존():
    draft, sections, problems, sources = fixture()
    original = copy.deepcopy(sections)
    request = prepare_evidence_correction(draft, sections, problems, sources)
    result = apply_evidence_correction(json.dumps(response(request)), request, sources)
    assert result["draft"] == draft
    expected = copy.deepcopy(original)
    expected[0]["근거"][0]["인용문"] = (
        "첫 사건의 기록은 남아 있다. 다음 사건은 별도로 기록되어 있다."
    )
    assert result["sections"] == expected
    assert (
        result["changes"][0]["before"]
        == result["changes"][0]["after"]
        == "첫 사건의 기록은 남아 있다."
    )
    assert sections == original
    assert request.response_schema["additionalProperties"] is False


def test_세문제까지명시허용_기존수동기본은두문제():
    from 서버.보고서보완 import prepare_correction

    draft, sections, problems, sources = fixture()
    three = problems * 3
    with pytest.raises(ResearchContractError, match="problem count must be 1..2"):
        prepare_correction(draft, sections, three, sources)
    request = prepare_evidence_correction(draft, sections, three, sources)
    assert len(request.inputs["targets"]) == 1
    assert len(request.inputs["targets"][0]["문제"]) == 3


@pytest.mark.parametrize(
    "case,reason",
    [
        ("bool_target", "invalid response"),
        ("string_target", "invalid response"),
        ("bool_ref", "invalid response"),
        ("string_ref", "invalid response"),
        ("duplicate_ref", "invalid response"),
        ("unoffered", "invalid response"),
        ("empty_refs", "invalid response"),
        ("extra", "invalid response"),
        ("stale_id", "invalid response"),
        ("duplicate_key", "invalid response"),
        ("empty", "visible content"),
        ("hidden", "visible content"),
        ("reserved", "reserved citation"),
        ("encoded_reserved", "reserved citation"),
        ("source_changed", "source version changed"),
        ("noop", "no change"),
    ],
)
def test_엄격응답거절_입력객체불변(case, reason):
    draft, sections, problems, sources = fixture()
    request = prepare_evidence_correction(draft, sections, problems, sources)
    original = copy.deepcopy(sections)
    obj = response(request)
    item = obj["교정"][0]
    if case == "bool_target":
        item["대상번호"] = True
    elif case == "string_target":
        item["대상번호"] = "1"
    elif case == "bool_ref":
        item["근거구간"] = [True]
    elif case == "string_ref":
        item["근거구간"] = ["1"]
    elif case == "duplicate_ref":
        item["근거구간"] *= 2
    elif case == "unoffered":
        item["근거구간"] = [129]
    elif case == "empty_refs":
        item["근거구간"] = []
    elif case == "extra":
        item["extra"] = True
    elif case == "stale_id":
        obj["근거목록ID"] = "different request"
    elif case == "empty":
        item["내용"] = " "
    elif case == "hidden":
        item["내용"] = "```text\n숨긴 주장\n```"
    elif case == "reserved":
        item["내용"] = "사실 «자료»"
    elif case == "encoded_reserved":
        item["내용"] = "사실 &laquo;자료&raquo;"
    elif case == "source_changed":
        sources[next(iter(sources))] += " 변경"
    elif case == "noop":
        sections[0]["근거"][0]["인용문"] = next(iter(sources.values()))
        request = prepare_evidence_correction(draft, sections, problems, sources)
        obj = response(request)
        original = copy.deepcopy(sections)
    raw = json.dumps(obj)
    if case == "duplicate_key":
        raw = raw[:-1] + ',"교정": []}'
    with pytest.raises(ResearchContractError, match=reason):
        apply_evidence_correction(raw, request, sources)
    assert sections == original


@pytest.mark.parametrize(
    "case,reason",
    [
        ("four", "problem count"),
        ("wrong_type", "citation gaps required"),
        ("marker_drift", "noncanonical target paragraph"),
        ("adjacent", "noncanonical target paragraph"),
        ("stale_section", "section location"),
        ("duplicate", "ambiguous anchor"),
    ],
)
def test_준비거절_정확한기존문단필수(case, reason):
    draft, sections, problems, sources = fixture()
    if case == "four":
        problems *= 4
    elif case == "wrong_type":
        problems[0]["유형"] = "contradiction"
    elif case in ("marker_drift", "adjacent"):
        old = sections[0]["본문"]
        sections[0]["본문"] = old.replace(
            "»", "» 다른 문장" if case == "adjacent" else "» «다른출처»"
        )
        draft = draft.replace(old, sections[0]["본문"])
    elif case == "stale_section":
        sections[0]["본문"] += " 변경"
    elif case == "duplicate":
        draft += "\n\n" + plain_markdown(problems[0]["본문발췌"])
    with pytest.raises(ResearchContractError, match=reason):
        prepare_evidence_correction(draft, sections, problems, sources)


def test_요청사본변조와다른요청ID_교차응답거절():
    draft, sections, problems, sources = fixture()
    request = prepare_evidence_correction(draft, sections, problems, sources)
    raw = json.dumps(response(request))
    inputs = request.inputs
    inputs["catalog"][0]["text"] = "변조"
    inputs["targets"].clear()
    request.response_schema.clear()
    assert apply_evidence_correction(raw, request, sources)["draft"] == draft
    problems[0]["설명"] += " 다른 검토"
    other = prepare_evidence_correction(draft, sections, problems, sources)
    assert other.inputs["evidence_set_id"] != request.inputs["evidence_set_id"]
    with pytest.raises(ResearchContractError, match="invalid response"):
        apply_evidence_correction(raw, other, sources)


def test_다른절읽기구간은_그절이읽었어도_대상절에는금지():
    draft, sections, problems, sources = fixture()
    other = copy.deepcopy(sections[0])
    other.update(section_id="s2", 읽은문서=["별도자료"], 본문="다른 사건의 기록이다\\. «별도자료»")
    other["근거"] = [
        {
            "주장": "다른 사건의 기록이다.",
            "출처": "별도자료",
            "인용문": "다른 사건의 기록이 남아 있다.",
        }
    ]
    sources["별도자료"] = "다른 사건의 기록이 남아 있다."
    sections.append(other)
    draft += "\n\n" + other["본문"]
    problems.append(
        {
            "유형": "citation_gap",
            "설명": "추가 근거",
            "본문발췌": "다른 사건의 기록이다.",
            "출처": ["별도자료"],
        }
    )
    request = prepare_evidence_correction(draft, sections, problems, sources)
    refs = {e["source_id"]: e["id"] for e in request.inputs["catalog"]}
    obj = response(request)
    obj["교정"][0]["근거구간"] = [refs["별도자료"]]
    obj["교정"].append(
        {"대상번호": 2, "내용": "다른 사건의 기록이다.", "근거구간": [refs["별도자료"]]}
    )
    with pytest.raises(ResearchContractError, match="section source scope"):
        apply_evidence_correction(json.dumps(obj), request, sources)


def test_실패3차실제원본_읽기전용준비만_세지적매핑():
    from pathlib import Path

    from 서버.실제연구제어 import latest_sections

    root = Path(__file__).resolve().parents[3]
    archive = root / ".cache/보고서품질_검증/실제연구/context03/메인_차단아카이브.json"
    data = json.loads(archive.read_text(encoding="utf-8"))
    assert data["run"]["run_id"] == "5bf80ec206054e2692ee7a9ec9da6350"
    assert data["run"]["status"] == "blocked" and data["run"]["actual_model_requests"] == 21
    sections = latest_sections(
        [e["output"] for e in data["events"] if e["type"] == "research.completed"]
    )
    corpus = json.loads((root / "01_참고자료/원본데이터/corpus.json").read_text(encoding="utf-8"))[
        "docs"
    ]
    read = {key for s in sections for key in s["읽은문서"]}
    request = prepare_evidence_correction(
        data["run"]["draft"],
        sections,
        data["run"]["quality"]["semantic_review"]["문제"],
        {key: corpus[key] for key in read},
    )
    assert len(request.inputs["targets"]) == 3
    assert 1 <= len(request.inputs["catalog"]) <= 128
    assert all(10 <= e["end"] - e["start"] <= 400 for e in request.inputs["catalog"])
    assert len(json.dumps(request.inputs["catalog"], ensure_ascii=False).encode("utf-8")) <= 96000


def test_같은절두대상_교환해도_각원본관계와문단만교체():
    draft, sections, problems, sources = fixture()
    old_body = sections[0]["본문"]
    second_claim = "다음 사건은 별도로 기록되어 있다."
    sections[0]["본문"] += "\n\n" + plain_markdown(second_claim) + " «자료&amp;A\\_\\[1\\]»"
    sections[0]["근거"].append(
        {"주장": second_claim, "출처": next(iter(sources)), "인용문": "첫 사건의 기록은 남아 있다."}
    )
    draft = draft.replace(old_body, sections[0]["본문"])
    problems.append({**problems[0], "본문발췌": second_claim})
    request = prepare_evidence_correction(draft, sections, problems, sources)
    obj = response(request, second_claim)
    obj["교정"].append({"대상번호": 2, "내용": "첫 사건의 기록은 남아 있다.", "근거구간": [1]})
    result = apply_evidence_correction(json.dumps(obj), request, sources)
    assert [e["주장"] for e in result["sections"][0]["근거"]] == [
        second_claim,
        "첫 사건의 기록은 남아 있다.",
    ]
    assert len(result["changes"]) == 2


def test_절전체16관계상한_대상밖관계도포함():
    draft, sections, problems, sources = fixture()
    sources["추가자료"] = "추가 자료에도 첫 사건이 기록되어 있다."
    sections[0]["읽은문서"].append("추가자료")
    old = sections[0]["본문"]
    for n in range(15):
        claim = f"보존할 별도 주장{n}이다."
        sections[0]["본문"] += "\n\n" + plain_markdown(claim) + " «자료&amp;A\\_\\[1\\]»"
        sections[0]["근거"].append(
            {"주장": claim, "출처": "자료&A_[1]", "인용문": "첫 사건의 기록은 남아 있다."}
        )
    draft = draft.replace(old, sections[0]["본문"])
    request = prepare_evidence_correction(draft, sections, problems, sources)
    obj = response(request)
    obj["교정"][0]["근거구간"] = [e["id"] for e in request.inputs["catalog"]]
    assert len(obj["교정"][0]["근거구간"]) == 2
    with pytest.raises(ResearchContractError, match="section reference limit"):
        apply_evidence_correction(json.dumps(obj), request, sources)


def test_실제system_user_schema합산상한_모델호출전거절():
    draft, sections, problems, sources = fixture()
    problems[0]["설명"] = "한" * 100000
    with pytest.raises(ResearchContractError, match="input budget"):
        prepare_evidence_correction(draft, sections, problems, sources)


def test_선택출처표식변경_대상문단외바이트정확보존():
    draft, sections, problems, sources = fixture()
    sources["새자료_[2]"] = "첫 사건의 기록을 확인할 추가 자료가 남아 있다."
    sections[0]["읽은문서"].append("새자료_[2]")
    request = prepare_evidence_correction(draft, sections, problems, sources)
    obj = response(request, "첫 사건의 기록은 남아 있다.")
    obj["교정"][0]["근거구간"] = [
        next(e["id"] for e in request.inputs["catalog"] if e["source_id"] == "새자료_[2]")
    ]
    result = apply_evidence_correction(json.dumps(obj), request, sources)
    assert result["draft"] == draft.replace("«자료&amp;A\\_\\[1\\]»", "«새자료\\_\\[2\\]»")
    assert result["sections"][0]["근거"] == [
        {
            "주장": "첫 사건의 기록은 남아 있다.",
            "출처": "새자료_[2]",
            "인용문": "첫 사건의 기록을 확인할 추가 자료가 남아 있다.",
        }
    ]
    assert result["sections"][0]["읽은문서"] == sections[0]["읽은문서"]


def test_리터럴HTML은숨은본문이아니라_이스케이프된가시문장():
    # HTML 비활성 Markdown 계약: 주석처럼 생긴 입력도 escape하여 실제 표시한다.
    draft, sections, problems, sources = fixture()
    request = prepare_evidence_correction(draft, sections, problems, sources)
    obj = response(request, "<!-- 숨긴 주장 -->")
    result = apply_evidence_correction(json.dumps(obj), request, sources)
    assert result["sections"][0]["근거"][0]["주장"] == "<!-- 숨긴 주장 -->"
    assert "&lt;" in result["draft"] and "&gt;" in result["draft"]


def test_변조된private위치도_exact_slice_검사로거절():
    from dataclasses import replace

    draft, sections, problems, sources = fixture()
    request = prepare_evidence_correction(draft, sections, problems, sources)
    raw = json.dumps(response(request))
    c = request._draft_request._catalog[0]
    changed = (c[0], c[1], c[2] + 1, c[3], c[4])
    forged = replace(request, _draft_request=replace(request._draft_request, _catalog=(changed,)))
    with pytest.raises(ResearchContractError, match="span binding"):
        apply_evidence_correction(raw, forged, sources)


def test_인용순서만바꾼응답은_실제근거변경없는_noop거절():
    draft, sections, problems, sources = fixture()
    sources["추가자료"] = "첫 사건의 다른 자료가 함께 남아 있다."
    sections[0]["읽은문서"].append("추가자료")
    sections[0]["근거"][0]["인용문"] = sources["자료&A_[1]"]
    sections[0]["근거"].append(
        {"주장": "첫 사건의 기록은 남아 있다.", "출처": "추가자료", "인용문": sources["추가자료"]}
    )
    old = sections[0]["본문"]
    sections[0]["본문"] = old.replace("«자료&amp;A\\_\\[1\\]»", "«자료&amp;A\\_\\[1\\]» «추가자료»")
    draft = draft.replace(old, sections[0]["본문"])
    request = prepare_evidence_correction(draft, sections, problems, sources)
    obj = response(request)
    refs = {e["source_id"]: e["id"] for e in request.inputs["catalog"]}
    obj["교정"][0]["근거구간"] = [refs["추가자료"], refs["자료&A_[1]"]]
    with pytest.raises(ResearchContractError, match="no change"):
        apply_evidence_correction(json.dumps(obj), request, sources)


def test_검토자가다른절근거를지적해도_그원문을보완입력으로승격하지않음():
    draft, sections, problems, sources = fixture()
    problems[0]["근거문맥"] = [
        {
            "section_id": "untargeted",
            "source_id": "다른절자료",
            "text": "대상절은 읽지 않은 문서의 비공개 원문",
        }
    ]
    request = prepare_evidence_correction(draft, sections, problems, sources)
    assert "대상절은 읽지 않은 문서의 비공개 원문" not in request.prompts[1]
    assert request.inputs["targets"][0]["문제"] == [
        {
            "유형": "citation_gap",
            "설명": "첫 사건 기록의 구간을 인용해야 한다.",
            "본문발췌": "첫 사건의 기록은 남아 있다.",
        }
    ]
    assert request.inputs["catalog"][0]["source_id"] == "자료&A_[1]"


def test_반환요청의system도_준비시점불변스냅샷(monkeypatch):
    from 서버 import 근거보완

    request = prepare_evidence_correction(*fixture())
    before = request.prompts
    monkeypatch.setattr(근거보완, "SYSTEM", "다른 system 지침")
    assert request.prompts == before
    other = prepare_evidence_correction(*fixture())
    assert other.prompts[0] == "다른 system 지침"
    assert other.inputs["evidence_set_id"] != request.inputs["evidence_set_id"]
