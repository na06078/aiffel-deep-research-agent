"""원문을 바꾸지 않는 문장·문단 우선 근거 구간. 위치는 Python 문자 단위다."""

import re
from bisect import bisect_right

# 종결 부호 뒤의 닫는 따옴표와 공백도 앞 구간에 그대로 포함한다.
_BOUNDARY = re.compile(r"""[.!?。！？]+["'”’」』)\]]*(?:\s+|$)|\r\n|[\r\n]""")


def source_ranges(text: str) -> list[tuple[int, int]]:
    """10자 이상 원문의 전체 연속 분할(목표320, 최소10, 최대400)."""
    size = len(text)
    if size < 10:
        return []
    boundaries = [match.end() for match in _BOUNDARY.finditer(text)]
    ranges = []
    start = 0
    while size - start > 320:
        target = start + 320
        index = bisect_right(boundaries, target)
        if index and boundaries[index - 1] >= start + 10:
            end = boundaries[index - 1]
        elif index < len(boundaries) and boundaries[index] <= start + 400:
            end = boundaries[index]
        else:
            end = target
        if 0 < size - end < 10:
            # 짧은 꼬리는 병합 가능한 경우 병합하고, 아니면 앞에서 재분배한다.
            end = size if size - start <= 400 else size - 10
        ranges.append((start, end))
        start = end
    if start < size:
        ranges.append((start, size))
    return ranges
