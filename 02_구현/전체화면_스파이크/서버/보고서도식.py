"""보고서 SVG 데이터의 제한 프로파일 검증. 실행/자원 로딩은 하지 않는다."""

import math
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
ELEMENTS = {
    "svg",
    "g",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "path",
    "text",
    "tspan",
    "title",
    "desc",
}
GEOMETRY = {
    "svg": {"viewBox", "width", "height"},
    "rect": {"x", "y", "width", "height", "rx", "ry"},
    "circle": {"cx", "cy", "r"},
    "ellipse": {"cx", "cy", "rx", "ry"},
    "line": {"x1", "x2", "y1", "y2"},
    "polyline": {"points"},
    "polygon": {"points"},
    "path": {"d"},
    "text": {"x", "y", "dx", "dy"},
    "tspan": {"x", "y", "dx", "dy"},
}
ENUMS = {
    "stroke-linecap": {"butt", "round", "square"},
    "stroke-linejoin": {"miter", "round", "bevel"},
    "fill-rule": {"nonzero", "evenodd"},
    "text-anchor": {"start", "middle", "end"},
    "dominant-baseline": {
        "auto",
        "alphabetic",
        "middle",
        "central",
        "hanging",
        "text-before-edge",
        "text-after-edge",
    },
    "font-weight": {
        "normal",
        "bold",
        "100",
        "200",
        "300",
        "400",
        "500",
        "600",
        "700",
        "800",
        "900",
    },
    "font-style": {"normal", "italic", "oblique"},
    "font-family": {
        "sans-serif",
        "serif",
        "monospace",
        "Malgun Gothic",
        "맑은 고딕",
        "Arial",
        "Consolas",
    },
}
PAINT_NUMBERS = {
    "stroke-width",
    "stroke-miterlimit",
    "stroke-dashoffset",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
    "font-size",
    "letter-spacing",
}
COMMON = set(ENUMS) | PAINT_NUMBERS | {"fill", "stroke", "stroke-dasharray", "transform"}


def _numbers(value):
    if not re.fullmatch(rf"\s*{NUMBER}(?:(?:\s*,\s*|\s+){NUMBER})*\s*", value):
        raise SvgValidationError("SVG 숫자 형식이 올바르지 않습니다.")
    values = [float(v) for v in re.findall(NUMBER, value)]
    if any(not math.isfinite(v) or abs(v) > 100_000 for v in values):
        raise SvgValidationError("SVG 숫자 범위를 초과했습니다.")
    return values


def _color(value):
    if re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value):
        return
    if value.lower() in {
        "none",
        "transparent",
        "black",
        "white",
        "red",
        "green",
        "blue",
        "gray",
        "grey",
        "navy",
        "teal",
        "orange",
        "yellow",
        "purple",
        "pink",
        "brown",
        "silver",
        "maroon",
        "lime",
        "olive",
        "aqua",
        "fuchsia",
        "currentcolor",
    }:
        return
    match = re.fullmatch(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", value)
    if not match or any(int(v) > 255 for v in match.groups()):
        raise SvgValidationError("SVG 색 형식은 허용하지 않습니다.")


def _transform(value):
    position = 0
    count = 0
    for match in re.finditer(r"(translate|scale|rotate|matrix|skewX|skewY)\(([^()]*)\)", value):
        if value[position : match.start()].strip(" ,\t\n\r"):
            raise SvgValidationError("SVG transform 형식이 올바르지 않습니다.")
        sizes = {
            "translate": {1, 2},
            "scale": {1, 2},
            "rotate": {1, 3},
            "matrix": {6},
            "skewX": {1},
            "skewY": {1},
        }
        if len(_numbers(match[2])) not in sizes[match[1]]:
            raise SvgValidationError("SVG transform 인자 수가 올바르지 않습니다.")
        position = match.end()
        count += 1
    if not count or value[position:].strip():
        raise SvgValidationError("SVG transform 형식이 올바르지 않습니다.")


def _path(value):
    # 토큰의 전체 구간을 소비하고 명령별 인자 수/arc 플래그를 검사한다.
    tokens = []
    position = 0
    for match in re.finditer(rf"[MmZzLlHhVvCcSsQqTtAa]|{NUMBER}", value):
        gap = value[position : match.start()]
        if gap.strip(" ,\t\n\r") or (
            "," in gap
            and (gap.count(",") != 1 or not tokens or tokens[-1].isalpha() or match[0].isalpha())
        ):
            raise SvgValidationError("SVG path 문법이 올바르지 않습니다.")
        tokens.append(match[0])
        position = match.end()
    if value[position:].strip():
        raise SvgValidationError("SVG path 문법이 올바르지 않습니다.")
    if not tokens or tokens[0] not in {"M", "m"}:
        raise SvgValidationError("SVG path는 이동 명령으로 시작해야 합니다.")
    sizes = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}
    index = 0
    while index < len(tokens):
        command = tokens[index].upper()
        index += 1
        if command == "Z":
            continue
        if command not in sizes:
            raise SvgValidationError("SVG path 명령이 올바르지 않습니다.")
        end = index
        while end < len(tokens) and tokens[end] not in "MmZzLlHhVvCcSsQqTtAa":
            end += 1
        arguments = tokens[index:end]
        size = sizes[command]
        if not arguments or len(arguments) % size:
            raise SvgValidationError("SVG path 인자 수가 올바르지 않습니다.")
        values = _numbers(" ".join(arguments))
        if command == "A":
            for offset in range(0, len(values), 7):
                if min(values[offset : offset + 2]) < 0 or any(
                    arguments[offset + n] not in {"0", "1"} for n in (3, 4)
                ):
                    raise SvgValidationError("SVG arc 반경/플래그가 올바르지 않습니다.")
        index = end


def _attribute(tag, attr, value):
    if attr not in COMMON | GEOMETRY.get(tag, set()):
        raise SvgValidationError("SVG 속성은 허용하지 않습니다.")
    if attr in ENUMS:
        if value not in ENUMS[attr]:
            raise SvgValidationError("SVG 글자/선 속성 값은 허용하지 않습니다.")
    elif attr in {"fill", "stroke"}:
        _color(value)
    elif attr == "transform":
        _transform(value)
    elif attr == "d":
        _path(value)
    elif attr == "stroke-dasharray" and value == "none":
        return
    else:
        values = _numbers(value)
        if attr == "viewBox":
            if len(values) != 4 or min(values[2:]) <= 0:
                raise SvgValidationError("SVG viewBox 폭/높이는 양수여야 합니다.")
        elif attr == "points":
            if len(values) < 4 or len(values) % 2:
                raise SvgValidationError("SVG 좌표 쌍이 올바르지 않습니다.")
        elif attr == "stroke-dasharray":
            if min(values) < 0 or max(values) <= 0:
                raise SvgValidationError("SVG 선 간격이 올바르지 않습니다.")
        else:
            if len(values) != 1:
                raise SvgValidationError("SVG 숫자는 하나여야 합니다.")
            if (
                attr
                in {
                    "width",
                    "height",
                    "r",
                    "rx",
                    "ry",
                    "stroke-width",
                    "stroke-miterlimit",
                    "font-size",
                }
                and values[0] < 0
            ):
                raise SvgValidationError("SVG 크기는 음수일 수 없습니다.")
            if attr in {"opacity", "fill-opacity", "stroke-opacity"} and not 0 <= values[0] <= 1:
                raise SvgValidationError("SVG 투명도 범위가 올바르지 않습니다.")


class _BoundedTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = self.count = 0

    def start(self, tag, attrs):
        self.depth += 1
        self.count += 1
        if self.depth > 12 or self.count > 300:
            raise SvgValidationError("SVG 요소/깊이 한도를 초과했습니다.")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name, pubid, system):
        raise SvgValidationError("SVG DTD/entity는 허용하지 않습니다.")

    def pi(self, target, text):
        raise SvgValidationError("SVG 처리 지시문은 허용하지 않습니다.")

    def start_ns(self, prefix, uri):
        if uri not in {"", SVG_NS}:
            raise SvgValidationError("SVG namespace는 허용하지 않습니다.")


class SvgValidationError(ValueError):
    """실행 가능한 콘텐츠 또는 제한 프로파일 위반."""


def validate_svg(code: str) -> str:
    if not isinstance(code, str):
        raise SvgValidationError("SVG 코드는 문자열이어야 합니다.")
    try:
        if len(code.encode("utf-8")) > 32 * 1024:
            raise SvgValidationError("SVG 코드 32KiB 한도를 초과했습니다.")
        # DTD 콜백이 선언 시 즉시 차단하므로 entity 확장 전에 중단한다.
        root = ET.fromstring(code, parser=ET.XMLParser(target=_BoundedTree()))
    except (ET.ParseError, UnicodeError) as error:
        raise SvgValidationError("SVG XML 형식이 올바르지 않습니다.") from error

    path_size = 0

    def copy_node(node):
        nonlocal path_size
        tag = node.tag.removeprefix("{" + SVG_NS + "}")
        if tag not in ELEMENTS or (tag == "svg" and node is not root):
            raise SvgValidationError("SVG 요소는 허용하지 않습니다.")
        result = ET.Element(tag)
        for attr, value in node.attrib.items():
            if attr == "d":
                path_size += len(value)
                if path_size > 12_000:
                    raise SvgValidationError("SVG path 합계 한도를 초과했습니다.")
            _attribute(tag, attr, value)
            result.set(attr, value)
        result.text, result.tail = node.text, node.tail
        result.extend(copy_node(child) for child in node)
        return result

    clean = copy_node(root)
    if clean.tag != "svg" or "viewBox" not in clean.attrib:
        raise SvgValidationError("SVG 루트에는 viewBox가 필요합니다.")
    clean.set("xmlns", SVG_NS)
    return ET.tostring(clean, encoding="unicode")
