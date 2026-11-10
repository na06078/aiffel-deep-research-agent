"""종합 제공자 계약: I/O 없이 실제 읽은 출처에 결합한다."""

SYNTHESIS_PROTOCOL = "source-bound-synthesis-v1"


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
                        "코드": {"type": "string"},
                        "출처": provenance(),
                    }
                ),
            },
            "시각화생략사유": {"type": "string"},
        }
    )
