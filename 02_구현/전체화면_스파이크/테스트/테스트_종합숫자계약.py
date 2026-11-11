"""생성 전 SVG 숫자 어휘 경계. 실제 제공자/네트워크 호출은 하지 않는다."""

import json
import re
from pathlib import Path

import pytest

from 서버.보고서도식 import SvgValidationError, validate_svg
from 서버.종합계약 import build_synthesis_schema

PROJECT = Path(__file__).resolve().parents[3]
PREFLIGHT = PROJECT / ".cache/보고서품질_검증/실모델점검"


def accepts(code):
    contract = build_synthesis_schema(["임오군란"])
    field = contract["properties"]["도식"]["items"]["properties"]["코드"]
    # JSON Schema에서 pattern 부재는 모든 문자열을 허용한다.
    return "pattern" not in field or re.search(field["pattern"], code) is not None


def stored_code(run):
    path = PREFLIGHT / run / "응답_synthesis.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return json.loads(payload["content"])["도식"][0]["코드"]


def test_실제모델의_fifty_y를_생성스키마에서_거부한다():
    code = stored_code("live-611fe2621f6b402f822d52bfaa5f0010")
    assert code.count("y=' fifty'") == 2
    with pytest.raises(SvgValidationError, match="숫자 형식"):
        validate_svg(code)
    assert not accepts(code), "실제 y=' fifty'가 생성 스키마의 코드 문자열을 통과했습니다."


def test_첫_실모델의_원래_성공_SVG를_그대로_허용한다():
    code = stored_code("live-425a3b480812421ab238e131d729e43d")
    assert validate_svg(code)
    assert accepts(code)


def test_기존_허용_프로파일_고정문자열들을_모두_허용한다():
    import ast

    original = Path(__file__).with_name("테스트_보고서도식.py")
    tree = ast.parse(original.read_text(encoding="utf-8"))
    checked = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            code = node.value
            if "<svg" not in code and "<s:svg" not in code:
                continue
            try:
                validate_svg(code)
            except SvgValidationError:
                continue
            checked.append(code)
            assert accepts(code), code
    assert len(checked) >= 5
    assert any("skewY(-5)" in code and "<s:tspan" in code for code in checked)


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-0",
        "+.5",
        "1.",
        "-2.5e+2",
        " 50 ",
        "&#53;0",
        "&#x35;0",
        "&#43;&#46;5",
        "1&#101;&#45;2",
    ],
)
def test_기존_ASCII_숫자문법과_XML문자참조를_허용한다(value):
    code = f'<svg viewBox="0 0 600 180"><text y="{value}">fifty는 본문에서는 허용</text></svg>'
    assert validate_svg(code)
    assert accepts(code)


@pytest.mark.parametrize(
    "space",
    [
        "\u0085",
        "\u00a0",
        "\u1680",
        "\u200a",
        "\u2028",
        "\u2029",
        "\u202f",
        "\u205f",
        "\u3000",
        "&#x200A;",
        "&#8239;",
    ],
)
def test_기존_숫자목록의_Unicode공백도_허용한다(space):
    code = f'<svg viewBox="0{space}0{space}600{space}180"><text y="{space}50{space}"/></svg>'
    assert validate_svg(code)
    assert accepts(code)


@pytest.mark.parametrize(
    "attribute",
    [
        "x",
        "y",
        "dx",
        "dy",
        "width",
        "height",
        "rx",
        "ry",
        "cx",
        "cy",
        "r",
        "x1",
        "x2",
        "y1",
        "y2",
        "stroke-width",
        "stroke-miterlimit",
        "stroke-dashoffset",
        "opacity",
        "fill-opacity",
        "stroke-opacity",
        "font-size",
        "letter-spacing",
        "viewBox",
        "points",
        "stroke-dasharray",
    ],
)
@pytest.mark.parametrize("quote", ["'", '"'])
def test_숫자속성은_일반문자열_분기로_우회하지_못한다(attribute, quote):
    # 요소별 속성 유효성은 이 테스트보다 뒤에 있는 기존 트리 검증의 책임이다.
    code = f'<svg viewBox="0 0 600 180"><text {attribute}={quote} fifty{quote}>설명</text></svg>'
    assert not accepts(code)


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("viewBox", "0,,0 1 1"),
        ("points", "0 0 fifty 1"),
        ("stroke-dasharray", "1 fifty"),
        ("y", "NaN"),
        ("y", "Infinity"),
        ("y", "50px"),
        ("y", "0 1"),
        ("y", "０"),
        ("y", "&#102;ifty"),
    ],
)
def test_잘못된_숫자_어휘를_거부한다(attribute, value):
    assert not accepts(f'<svg viewBox="0 0 600 180"><text {attribute}="{value}"/></svg>')


def test_모든_선택속성과_글꼴_값_문자열을_보존한다():
    from 서버.보고서도식 import ENUMS

    for name, values in ENUMS.items():
        for value in values:
            code = f'<svg viewBox="0 0 600 180"><text {name}="{value}">한글 &amp; 설명</text></svg>'
            assert validate_svg(code)
            assert accepts(code)
    code = """<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0,0 600,180">
    <!-- 숫자가 아닌 fifty 주석도 그대로 보존 -->
    <g stroke-dasharray="none" stroke-miterlimit="4" stroke-dashoffset="-.5" letter-spacing="1e-2" fill-opacity=".5" stroke-opacity="1">
    <polyline points="0&#44;0 10&#32;20"/><text font-family="맑은&#32;고딕"><![CDATA[<y=' fifty'>]]></text></g></svg>"""
    assert validate_svg(code)
    assert accepts(code)


@pytest.mark.parametrize("text", ["]", "]]", "]]]", "]>", "]]a", "]]]a", "<y=' fifty'>]"])
def test_CDATA의_닫는대괄호_본문도_기존대로_허용한다(text):
    code = f'<svg viewBox="0 0 1 1"><text><![CDATA[{text}]]></text></svg>'
    assert validate_svg(code)
    assert accepts(code)


def test_구조_보안_범위_검증은_여전히_후단의_책임이다():
    cases = [
        ('<svg viewBox="0 0 1 1"><text y="100001"/></svg>', "숫자 범위"),
        ('<svg viewBox="0 0 1 1"><g transform="rotate(1 2)"/></svg>', "transform 인자 수"),
        ('<svg viewBox="0 0 1 1"><path d="M0 0 L1"/></svg>', "path 인자 수"),
        ('<svg viewBox="0 0 1 1"><rect fill="url(https://invalid/x)"/></svg>', "색 형식"),
        ('<svg viewBox="0 0 1 1"><rect opacity="2"/></svg>', "투명도 범위"),
        ('<svg viewBox="0 0 1 1"><polygon points="0 0 1"/></svg>', "좌표 쌍"),
    ]
    for code, reason in cases:
        assert accepts(code)
        with pytest.raises(SvgValidationError, match=reason):
            validate_svg(code)


def test_스키마는_독립된_양의_정규문법만_추가한다():
    first = build_synthesis_schema(["임오군란"])
    second = build_synthesis_schema(["임오군란"])
    field = first["properties"]["도식"]["items"]["properties"]["코드"]
    pattern = field["pattern"]
    assert pattern.startswith("^") and pattern.endswith("$")
    assert len(pattern) < 12000
    assert not re.search(r"\(\?(?!:)|\\[1-9]", pattern)
    assert set(field) == {"type", "pattern"}
    assert first == second
    field["pattern"] = "changed"
    assert second["properties"]["도식"]["items"]["properties"]["코드"]["pattern"] == pattern
