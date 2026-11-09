"""SVG 공개 경계: XML 재파싱과 독립 제한값으로 확인한다."""

import xml.etree.ElementTree as ET

import pytest

from 서버.보고서도식 import SvgValidationError, validate_svg


def test_한글_도형을_새_SVG표준트리로_직렬화한다():
    source = '<svg viewBox="0 0 320 120"><title>한글 흐름</title><desc>단계 설명</desc><rect x="1" y="2" width="300" height="100" fill="#eef2ff"/><text x="20" y="55">자료 → 검토</text></svg>'
    result = validate_svg(source)
    root = ET.fromstring(result)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib["viewBox"] == "0 0 320 120"
    assert "자료 → 검토" in "".join(root.itertext())
    assert result != source


def test_허용_도형과_선_글자_path_transform_표준namespace():
    code = """<s:svg xmlns:s="http://www.w3.org/2000/svg" viewBox="-10 -20 600 300" width="600" height="300">
    <s:g transform="translate(10,20) rotate(15 50 50) scale(.8) matrix(1 0 0 1 0 0) skewX(5) skewY(-5)" fill="none" stroke="navy" stroke-width="2" opacity="0.8">
    <s:rect x="0" y="0" width="100" height="50" rx="4" ry="4"/>
    <s:circle cx="20" cy="30" r="10"/><s:ellipse cx="20" cy="40" rx="5" ry="10"/>
    <s:line x1="0" y1="0" x2="100" y2="100" stroke-linecap="round" stroke-dasharray="2 4"/>
    <s:polyline points="0,0 10,20 30,10" stroke-linejoin="round"/>
    <s:polygon points="0,0 10,20 30,10" fill="rgb(20, 30, 40)" fill-rule="evenodd"/>
    <s:path d="M0 0 L10 10 h2 v-2 C1 2 3 4 5 6 S7 8 9 10 Q1 2 3 4 T5 6 A10 20 0 0 1 30 40 z"/>
    <s:text x="15" y="25" font-size="18" font-family="sans-serif" font-weight="bold" text-anchor="middle" dominant-baseline="middle"><s:tspan dx="1" dy="2" font-style="italic">한글</s:tspan></s:text>
    </s:g></s:svg>"""
    root = ET.fromstring(validate_svg(code))
    assert len(list(root.iter())) == 11
    assert "한글" in "".join(root.itertext())


@pytest.mark.parametrize(
    "payload",
    [
        "<script/>",
        "<foreignObject/>",
        "<image/>",
        "<use/>",
        "<a/>",
        "<style/>",
        "<animate/>",
        "<filter/>",
        "<iframe/>",
        '<svg xmlns="urn:evil"/>',
        '<g xmlns:unused="urn:evil"/>',
        '<rect onload="alert(1)"/>',
        '<rect href="https://invalid/x"/>',
        '<rect style="fill:red"/>',
        '<rect fill="url(https://invalid/x)"/>',
        '<rect stroke="url(#x)"/>',
        '<rect xmlns:x="http://www.w3.org/1999/xlink" x:href="#x"/>',
        '<rect x="NaN"/>',
        '<rect x="1e309"/>',
        '<rect x="100001"/>',
        '<rect width="-1"/>',
        '<circle r="-2"/>',
        '<rect fill="red; color:blue"/>',
        '<text font-family="url(https://invalid/font)">x</text>',
        '<g transform="translate(1) garbage"/>',
        '<g transform="rotate(1 2)"/>',
        '<g transform="matrix(1 0 0 1 0 100001)"/>',
        '<path d="M 1 2 BAD"/>',
        '<path d="L1 2"/>',
        '<path d="M0 0 L1"/>',
        '<path d="M0 0 A1 2 0 2 0 10 20"/>',
        '<path d="M0 0 A-1 2 0 0 0 10 20"/>',
        '<path d="M0 0 L100001 0"/>',
        '<polyline points="0 0 1"/>',
        '<rect opacity="1.1"/>',
        '<rect stroke-dasharray="-1 2"/>',
        '<svg viewBox="0 0 1 1"/>',
    ],
)
def test_금지_요소와_속성_문법을_거부한다(payload):
    with pytest.raises(SvgValidationError):
        validate_svg('<svg viewBox="0 0 400 200">' + payload + "</svg>")


@pytest.mark.parametrize(
    "code",
    [
        '<!DOCTYPE svg [<!ENTITY x "expanded">]><svg viewBox="0 0 1 1">&x;</svg>',
        '<!DOCTYPE svg SYSTEM "file:///secret"><svg viewBox="0 0 1 1"/>',
        '<?xml-stylesheet href="https://invalid/x"?><svg viewBox="0 0 1 1"/>',
        '<svg viewBox="0 0 0 1"/>',
        '<svg viewBox="0 0 1 -1"/>',
        "<svg/>",
        '<svg viewBox="0 0 1 1"><text>&unknown;</text></svg>',
        '<svg viewBox="0 0 1 1"><text>' + "한" * 11000 + "</text></svg>",
        '<svg viewBox="0 0 1 1">' + "<rect/>" * 300 + "</svg>",
        '<svg viewBox="0 0 1 1">' + "<g>" * 12 + "</g>" * 12 + "</svg>",
        '<svg viewBox="0 0 1 1"><path d="M0 0' + " L0 0" * 2400 + '"/></svg>',
        '<svg viewBox="0 0 1 1"><path d="M0 0'
        + " L0 0" * 1300
        + '"/><path d="M0 0'
        + " L0 0" * 1300
        + '"/></svg>',
    ],
)
def test_선언_루트와_자원한도를_거부한다(code):
    with pytest.raises(SvgValidationError):
        validate_svg(code)


def test_한도_바로안쪽과_텍스트_이스케이프는_보존한다():
    root = ET.fromstring(
        validate_svg(
            '<svg viewBox="-100000 0 100000 1">'
            + "<g>" * 10
            + "<text>&lt;script&gt; &amp; 설명</text>"
            + "</g>" * 10
            + "</svg>"
        )
    )
    assert "<script> & 설명" in "".join(root.itertext())
    assert (
        len(
            list(
                ET.fromstring(
                    validate_svg('<svg viewBox="0 0 1 1">' + "<rect/>" * 299 + "</svg>")
                ).iter()
            )
        )
        == 300
    )


def test_바이트와_path_정확한상한_및_빈namespace():
    base = '<svg viewBox="0 0 1 1"><!--PADDING--></svg>'
    exact = base.replace("PADDING", "x" * (32768 - len(base.encode()) + len("PADDING")))
    assert len(exact.encode()) == 32768 and validate_svg(exact)
    with pytest.raises(SvgValidationError, match="32KiB"):
        validate_svg(exact.replace("<!--", "<!--x"))
    path = "M0 0" + " L0 0" * 2399 + " "
    assert len(path) == 12000
    assert validate_svg('<svg viewBox="0 0 1 1"><path d="' + path + '"/></svg>')
    with pytest.raises(SvgValidationError, match="path 합계"):
        validate_svg('<svg viewBox="0 0 1 1"><path d="' + path + ' "/></svg>')
    assert validate_svg('<svg xmlns="" viewBox="0 0 1 1"/>')


@pytest.mark.parametrize(
    "attribute",
    [
        'viewBox="0,,0 1 1"',
        'viewBox="０ ０ １ １"',
    ],
)
def test_SVG숫자는_ASCII와_단일구분자만_허용한다(attribute):
    with pytest.raises(SvgValidationError, match="숫자 형식"):
        validate_svg("<svg " + attribute + "/>")


@pytest.mark.parametrize("d", ["M,0 0", "M0,,0", "M0 0,", "M0 0,L1 2"])
def test_path_잘못된_콤마를_거부한다(d):
    with pytest.raises(SvgValidationError, match="path 문법"):
        validate_svg('<svg viewBox="0 0 1 1"><path d="' + d + '"/></svg>')
