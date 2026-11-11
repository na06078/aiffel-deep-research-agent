"""기존 논리 문장 fixture를 v5 wire로 옮기는 테스트 전용 생성기.

잘못된 값은 형변환·중복 제거·절단하지 않는다. 누락 주근거는 null로 노출한다.
운영 응답 처리에는 사용하지 않는다.
"""
def citation_units(statements):
    if type(statements) is not list:
        return statements
    units = []
    for number, item in enumerate(statements, 1):
        if type(item) is not dict or set(item) != {"내용", "근거구간"} or type(item["근거구간"]) is not list:
            units.append(item)
            continue
        refs = item["근거구간"]
        units.append({"내용": item["내용"], "근거구간": refs[0] if refs else None})
        units.extend({"내용": None, "근거구간": ref} for ref in refs[1:])
    return units
