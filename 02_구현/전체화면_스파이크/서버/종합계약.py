"""종합 제공자 계약: I/O 없이 실제 읽은 출처에 결합한다."""

import re

from .보고서도식 import COMMON, ELEMENTS, ENUMS, GEOMETRY, NUMBER

SYNTHESIS_PROTOCOL = "source-bound-synthesis-v1"


def _svg_lexical_pattern():
    """양의 XML 토큰 문법만 제한한다. 트리/값의 의미 검사는 validate_svg가 맡는다.

    숫자가 아닌 속성은 XML 문자열을 그대로 허용하므로 글꼴/색/변환/path와
    XML 문자 참조의 기존 표현력을 줄이지 않는다. 숫자 속성은 별도 분기여서
    일반 문자열 분기로 빠질 수 없다. lookaround/backreference는 사용하지 않는다.
    """
    # XML 파싱 뒤 유효한 숫자가 되는 문자 참조도 숫자 토큰으로 인정한다.
    number = (
        NUMBER.replace("[0-9]", r"(?:[0-9]|&#0*(?:4[89]|5[0-7]);|&#x0*3[0-9];)")
        .replace("[+-]", r"(?:[+-]|&#0*4[35];|&#x0*2[bBdD];)")
        .replace(r"\.", r"(?:\.|&#0*46;|&#x0*2[eE];)")
        .replace("[eE]", r"(?:[eE]|&#0*(?:69|101);|&#x0*(?:45|65);)")
    )
    # RE2의 \\s는 ASCII이므로 Python _numbers가 받는 Unicode 공백도 명시한다.
    whitespace = r"[\s" + "\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"
    space = (
        "(?:"
        + whitespace
        + r"|&#0*(?:9|10|13|32|133|160|5760|819[2-9]|820[0-2]|823[23]|8239|8287|12288);"
        r"|&#x0*(?:9|[aAdD]|20|85|[aA]0|1680|200[0-9aA]|202[89]|202[fF]|205[fF]|3000);)"
    )
    comma = r"(?:,|&#0*44;|&#x0*2[cC];)"
    separator = rf"(?:{space}*{comma}{space}*|{space}+)"
    scalar = rf"{space}*{number}{space}*"
    numbers = rf"{space}*{number}(?:{separator}{number})*{space}*"
    attributes = COMMON | set().union(*GEOMETRY.values())
    lists = {"viewBox", "points", "stroke-dasharray"}
    numeric = attributes - {"d", "transform", "fill", "stroke"} - lists - set(ENUMS)

    def names(values):
        return "(?:" + "|".join(re.escape(v) for v in sorted(values)) + ")"

    def quoted(value):
        return '(?:"' + value + "\"|'" + value + "')"

    assignment = r"\s*=\s*"
    branches = [
        names(numeric) + assignment + quoted(scalar),
        names({"viewBox", "points"}) + assignment + quoted(numbers),
        "stroke-dasharray" + assignment + quoted("(?:none|" + numbers + ")"),
        # 문자/색/enum/transform/path의 값과 namespace URI 의미는 기존 검증 유지.
        names(attributes - numeric - lists) + assignment + r"""(?:"[^"<]*"|'[^'<]*')""",
        r"""xmlns(?::[^\s/:<>=]+)?\s*=\s*(?:"[^"<]*"|'[^'<]*')""",
    ]
    tag = r"(?:[^\s/:<>=]+:)?" + names(ELEMENTS)
    opening = "<" + tag + r"(?:\s+(?:" + "|".join(branches) + r"))*\s*/?>"
    closing = "</" + tag + r"\s*>"
    comment = r"<!--(?:[^-]|-[^-])*-->"
    cdata = r"<!\[CDATA\[(?:[^\]]|\][^\]]|\]\]+[^>\]])*\]*\]\]>"
    declaration = r"""<\?xml\s+(?:[^?]|\?[^>])*\?>"""
    alternatives = f"{opening}|{closing}|{comment}|{cdata}|{declaration}"
    return f"^(?:[^<]|{alternatives})*$"


def build_synthesis_schema(source_ids):
    """매 호출마다 독립된 plain schema를 반환한다."""
    if isinstance(source_ids, (str, bytes)):
        raise ValueError("invalid_source_ids")  # noqa: TRY004 - 출처 목록 계약 위반을 동일 코드로 거절한다.
    try:
        supplied = list(source_ids)
    except TypeError as error:
        raise ValueError("invalid_source_ids") from error
    if any(not isinstance(key, str) or not key.strip() for key in supplied):
        raise ValueError("invalid_source_ids")
    keys = sorted(set(supplied))

    def closed(properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    def strings(width):
        return {
            "type": "array",
            "items": {"type": "string"},
            "minItems": width,
            "maxItems": width,
        }

    def provenance():
        # 빈 출처에는 시각 배열 자체를 금지한다. 가짜 출처 enum은 만들지 않는다.
        item = {"type": "string"}
        if keys:
            item["enum"] = list(keys)
        return {"type": "array", "minItems": 1, "items": item}

    branches = []
    for width in range(2, 7):
        branches.append(
            closed(
                {
                    "제목": {"type": "string"},
                    "열": strings(width),
                    "행": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 10,
                        "items": closed({"셀": strings(width), "출처": provenance()}),
                    },
                }
            )
        )
    return closed(
        {
            "머리말": {"type": "string"},
            "맺음말": {"type": "string"},
            "표": {"type": "array", "maxItems": 2 if keys else 0, "items": {"anyOf": branches}},
            "도식": {
                "type": "array",
                "maxItems": 2 if keys else 0,
                "items": closed(
                    {
                        "제목": {"type": "string"},
                        "설명": {"type": "string"},
                        "코드": {"type": "string", "pattern": _svg_lexical_pattern()},
                        "출처": provenance(),
                    }
                ),
            },
            "시각화생략사유": {"type": "string"},
        }
    )
