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


@pytest.mark.parametrize(
    ("actual_limit", "expected_limit", "money", "expected_money", "reason"),
    [
        (245, 245, 2, 2, "조회만 검사하려면 --run-id가 필요합니다"),
        (208, 208, 2, 2, "조회만 검사하려면 --run-id가 필요합니다"),
        (208, 245, 2, 2, "공유 장부의 호출/금액 한도가 승인값과 다릅니다"),
        (245, 208, 2, 2, "공유 장부의 호출/금액 한도가 승인값과 다릅니다"),
        (245, 245, 3, 2, "공유 장부의 호출/금액 한도가 승인값과 다릅니다"),
        (10000, 10000, 20, 20, "조회만 검사하려면 --run-id가 필요합니다"),
        (10000, 10000, 2, 20, "공유 장부의 호출/금액 한도가 승인값과 다릅니다"),
        (10000, 10000, 20, 2, "공유 장부의 호출/금액 한도가 승인값과 다릅니다"),
    ],
)
def test_실제연구검사기는_승인한245회장부에서_조회검사경계까지_진입한다(
    monkeypatch, tmp_path, actual_limit, expected_limit, money, expected_money, reason
):
    import importlib
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(I / "검증"))
    driver = importlib.import_module("보고서품질_실제연구검사")
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    snapshot = {
        "provider": "openai",
        "mode": "research",
        "budget": {"max_attempts": actual_limit, "max_usd": money, "unknown_count": 4},
    }
    monkeypatch.setattr(driver, "get", lambda *_args: snapshot)
    args = SimpleNamespace(
        base_url="http://127.0.0.1:8797",
        data_dir=tmp_path / "03_실행데이터/보고서품질_근거문맥실제연구",
        expected_unknown=4,
        expected_max_attempts=expected_limit,
        expected_max_usd=expected_money,
        label="approved245",
        submit=False,
        run_id=None,
    )
    # 실제 제공자/브라우저를 시작하기 전의 다음 독립 guard까지 가야 한다.
    with pytest.raises(AssertionError, match=reason):
        driver.main(args)


def test_허용캐시밖_사전파일은_읽기전에_거절한다(monkeypatch, tmp_path):
    import importlib

    monkeypatch.syspath_prepend(str(I / "검증"))
    driver = importlib.import_module("보고서품질_실제연구검사")
    active_probe = importlib.import_module("보고서품질_실모델점검")
    monkeypatch.setattr(active_probe, "ROOT", tmp_path / "project")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    reads = []
    original = Path.read_text

    def tracked(path, *args, **kwargs):
        if path.resolve() == outside.resolve():
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked)
    with pytest.raises(AssertionError, match="preflight_control_invalid"):
        driver.validate_preflight(outside, 4)
    assert reads == [], "범위 밖의 JSON/인증 파일을 먼저 열고 나중에 거절하면 안 된다."


@pytest.mark.parametrize(
    "mutation", ["old", "protocol", "hash", "slice", "quote", "claim", "semantic"]
)
def test_submit_refuses_control_provenance_before_any_side_effect(monkeypatch, tmp_path, mutation):
    import importlib
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(I / "검증"))
    driver = importlib.import_module("보고서품질_실제연구검사")
    work = tmp_path / "fixture"
    result = asyncio.run(probe.run_probe(model=ModelDouble(), work=work))
    assert result["passed"], result
    result.update(mode="live", live_verified=True)
    if mutation == "old":
        result.pop("control_protocol", None)
    elif mutation == "protocol":
        result["control_protocol"] = "generated-positive-v0"
    elif mutation == "semantic":
        result["generated_candidate_semantic_verified"] = True
    else:
        key, value = {
            "hash": ("source_sha256", "0" * 64),
            "slice": ("end", 58),
            "quote": ("quote", "조작된 원문 인용이다."),
            "claim": ("positive_claim", "임의 생성 주장이다."),
        }[mutation]
        result.setdefault("control_source", {})[key] = value
    target = work / "실행메타데이터.json"
    target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    effects = []

    def forbidden_get(*_):
        effects.append("get")
        raise AssertionError("forbidden_get_before_control_gate")

    monkeypatch.setattr(driver, "get", forbidden_get)
    monkeypatch.setattr(driver, "sync_playwright", lambda: effects.append("browser"))
    args = SimpleNamespace(
        base_url="http://127.0.0.1:8797",
        data_dir=tmp_path / "03_실행데이터/보고서품질_test",
        expected_unknown=4,
        expected_max_attempts=245,
        label="rejected",
        submit=True,
        preflight=target,
    )
    with pytest.raises(AssertionError, match="preflight_control_invalid"):
        driver.main(args)
    assert effects == []
    assert not (tmp_path / ".cache/보고서품질_검증/실제연구/rejected").exists()


@pytest.fixture
def gate_bundle(tmp_path, monkeypatch):
    """명시 ModelDouble의 전송 형식 대역. live 플래그는 게이트 시험 입력일 뿐 실호출 아님."""
    import importlib

    monkeypatch.syspath_prepend(str(I / "검증"))
    driver = importlib.import_module("보고서품질_실제연구검사")

    class ReceiptFormatModelDouble(ModelDouble):
        async def complete(self, *args, on_attempt, **kwargs):
            index = len(self.calls)
            attempt = f"{index + 1:032x}"
            result = await super().complete(
                *args, on_attempt=lambda _: on_attempt(attempt), **kwargs
            )
            result.update(attempt_id=attempt, request_id="req_test_double_" + str(index))
            return result

        def usage(self):
            return {**super().usage(), "unknown_count": 4}

    work = tmp_path / "bundle"
    value = asyncio.run(probe.run_probe(model=ReceiptFormatModelDouble(), work=work))
    assert value["passed"], value
    value.update(mode="live", live_verified=True)
    target = work / "실행메타데이터.json"
    target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return driver, target


@pytest.mark.parametrize(
    "mutation",
    [
        "valid",
        "schema",
        "review_id",
        "source",
        "quote",
        "draft",
        "response",
        "negative_excerpt",
        "other_problem",
        "receipts",
        "duplicate_receipt",
        "attempt_binding",
        "usage",
        "unknown",
        "hashes",
        "fake",
    ],
)
def test_submit_gate_validates_actual_bound_bundle(gate_bundle, mutation, monkeypatch, tmp_path):
    driver, target = gate_bundle

    def change(name, transform):
        path = target.parent / name
        value = json.loads(path.read_text("utf-8"))
        transform(value)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    if mutation == "schema":
        change("입력_positive.json", lambda v: v.update(response_schema={}))
    elif mutation == "review_id":
        change("입력_negative.json", lambda v: v["request_inputs"].update(review_set_id="wrong"))
    elif mutation == "source":
        change(
            "입력_negative.json",
            lambda v: v["request_inputs"]["contexts"][0].update(source_hash="0" * 64),
        )
    elif mutation == "quote":
        change(
            "대조.json",
            lambda v: v["negative_sections"][0]["근거"][0].update(인용문="조작된 인용문"),
        )
    elif mutation == "draft":
        change("입력_positive.json", lambda v: v["request_inputs"].update(draft="가짜 생성 양성"))
    elif mutation in {"response", "negative_excerpt", "other_problem"}:

        def corrupt(v):
            reply = json.loads(v["content"])
            if mutation == "response":
                v["content"] = "not JSON"
                return
            reply["문제"][0].update(
                본문발췌="가시 본문에 없는 발췌" if mutation == "negative_excerpt" else "임오군란"
            )
            v["content"] = json.dumps(reply, ensure_ascii=False)

        change("응답_negative.json", corrupt)
    elif mutation == "receipts":
        change("호출영수증.json", lambda v: v.pop())
    elif mutation == "duplicate_receipt":
        change("호출영수증.json", lambda v: v[3].update(request_id=v[2]["request_id"]))
    elif mutation == "attempt_binding":
        change("시도.json", lambda v: v[3].update(attempt_id="0" * 32))
    elif mutation == "usage":
        change(target.name, lambda v: v["usage_after"].update(attempt_count=5))
    elif mutation == "unknown":
        change(target.name, lambda v: v["usage_after"].update(unknown_count=5))
    elif mutation == "hashes":
        change(target.name, lambda v: v.update(source_hashes={}))
    elif mutation == "fake":
        change(
            "호출영수증.json",
            lambda v: v[0].update(attempt_id="fake-0-0", request_id="fake-request-0"),
        )
    from types import SimpleNamespace

    monkeypatch.setattr(driver, "ROOT", tmp_path)
    effects = []

    def snapshot_boundary(*_):
        effects.append("get")
        raise AssertionError("validated_snapshot_boundary")

    monkeypatch.setattr(driver, "get", snapshot_boundary)
    monkeypatch.setattr(driver, "sync_playwright", lambda: pytest.fail("browser forbidden"))
    args = SimpleNamespace(
        base_url="http://127.0.0.1:8797",
        data_dir=tmp_path / "03_실행데이터/보고서품질_test",
        expected_unknown=4,
        expected_max_attempts=245,
        label="gate",
        submit=True,
        preflight=target,
    )
    if mutation == "valid":
        assert driver.validate_preflight(target, 4)["passed"] is True
        with pytest.raises(AssertionError, match="validated_snapshot_boundary"):
            driver.main(args)
        assert effects == ["get"]
    else:
        with pytest.raises(AssertionError, match="preflight_control_invalid"):
            driver.main(args)
        assert effects == []
        assert not (tmp_path / ".cache/보고서품질_검증/실제연구/gate").exists()
