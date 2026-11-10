"""정상 사전 점검 호출자가 문맥 검토 스키마를 실제로 전달하는지 확인한다.

실제 모델 대신 기존 명시 대역만 사용한다. 모델의 의미 판별력 증거는 아니다.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

I = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(I))
from 테스트.테스트_보고서품질_실모델점검 import ModelDouble, probe


class ReviewSchemaDouble(ModelDouble):
    async def complete(self, *args, **kwargs):
        result = await super().complete(*args, **kwargs)
        if kwargs["context"]["operation"] != "semantic_review":
            return result
        schema = kwargs.get("response_schema")
        if schema is None:
            return result
        props = schema["properties"]
        positive = kwargs["context"]["case"] == "positive"
        problem_schema = props["문제"]["items"]["properties"]
        refs = problem_schema["근거번호"]["items"]["enum"]
        result["content"] = json.dumps(
            {
                "검토목록ID": props["검토목록ID"]["enum"][0],
                "판정": "supported" if positive else "needs_revision",
                "문제": []
                if positive
                else [
                    {
                        "유형": "contradiction",
                        "설명": "명시 대역의 날짜 오류 판정",
                        "본문발췌": "1883년",
                        "근거번호": [refs[0]],
                    }
                ],
                "검토요약": "계약 전달 대역이며 모델 판별력 증거가 아님",
            },
            ensure_ascii=False,
        )
        return result


@pytest.fixture(autouse=True)
def no_real_credentials_or_ledger(monkeypatch):
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path.name != ".env" and path.suffix != ".sqlite3"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    monkeypatch.setitem(sys.modules, "서버.실제모델", None)
    monkeypatch.setitem(sys.modules, "서버.연구앱", None)


def test_사전4호출은_새문맥검토의_스키마를_제공자경계까지_전달한다(tmp_path):
    model = ReviewSchemaDouble()
    result = asyncio.run(probe.run_probe(model=model, work=tmp_path))
    semantic = [call for call in model.calls if call["context"]["operation"] == "semantic_review"]
    assert semantic, "normal caller did not reach semantic request"
    assert all(call["response_schema"] is not None for call in semantic), (
        "semantic request schema missing"
    )
    assert len(semantic) == 2
    assert all(
        set(call["response_schema"]["required"]) == {"검토목록ID", "판정", "문제", "검토요약"}
        for call in semantic
    )
    assert result["passed"], result
    assert result["observed_attempts"] == result["completed_calls"] == 4
    assert model.closed


def budget_case(width):
    source = "\n".join(
        f"{number:04d}번 문맥은 사건의 배경과 서로 다른 사실 관계를 설명한다. 기록을 대조해야 한다."
        for number in range(800)
    )
    quote = source.splitlines()[0]
    claim = quote + " " + "x" * width
    sections = [
        {
            "section_id": f"s{number}",
            "본문": claim + " «자료»",
            "충분": True,
            "부족": "",
            "읽은문서": ["자료"],
            "근거": [{"주장": claim, "출처": "자료", "인용문": quote}],
        }
        for number in range(4)
    ]
    sources = {section["section_id"]: {"자료": source} for section in sections}
    draft = "# 보고서\n" + "\n\n".join(section["본문"] for section in sections)
    return draft, sections, sources, quote


def test_추가문맥때문에만_넘는입력은_원고와인용을_보존하며_문맥만줄인다():
    from 서버.검토문맥 import build_review_request
    from 서버.교안연구 import ResearchContractError

    draft, sections, sources, quote = budget_case(16000)
    original = json.dumps(sections, ensure_ascii=False)
    try:
        request = build_review_request(draft, sections, sources)
    except ResearchContractError as error:
        pytest.fail(f"선택 문맥 때문에만 검토 전체가 거절됨: {error}")
    metadata = request.inputs
    assert metadata["draft"] == draft
    assert json.dumps(sections, ensure_ascii=False) == original
    assert metadata["scope"]["incomplete"] and metadata["scope"]["omitted"] > 0
    assert metadata["scope"]["selected"] > 0, "넣을 수 있는 문맥까지 전부 버리지 않는다"
    original_contexts = [context for context in metadata["contexts"] if context["kind"] == "quote"]
    assert len(original_contexts) == 4
    assert all(context["text"] == quote for context in original_contexts)
    size = sum(len(part.encode()) for part in request.prompts)
    size += (
        len(json.dumps(request.response_schema, ensure_ascii=False, allow_nan=False).encode())
        + 8192
    )
    assert size <= 272000


def test_원고와원래인용만으로_초과하면_원고를자르지않고_거절한다():
    from 서버.검토문맥 import build_review_request
    from 서버.교안연구 import ResearchContractError

    draft, sections, sources, _ = budget_case(50000)
    original = json.dumps([draft, sections], ensure_ascii=False)
    with pytest.raises(ResearchContractError, match="input_too_large"):
        build_review_request(draft, sections, sources)
    assert json.dumps([draft, sections], ensure_ascii=False) == original
