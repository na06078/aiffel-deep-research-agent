"""실제 역사·교안과 분리한 합성 시험 문서 네 건. 사실 답변이 아니다."""

DOCUMENTS = {
    f"합성문서-{i}": (
        f"이것은 합성문서-{i}의 시험용 원문이다. 실제 인물·역사·사건을 설명하지 않는다. "
        "이 자료는 로컬 병렬 작업과 지시 변경, 파일 바이트 검증을 위한 고정 입력이다. "
        "지시의 의미를 이해하거나 실제 리서치 정확성을 입증하는 근거가 아니다. "
        "문서에 적힌 시험 표식만 인용하며 이전 AI 보고서는 일차 자료로 사용하지 않는다."
    )
    for i in range(1, 5)
}


def evaluate_candidate(markdown):
    reasons = []
    if not markdown.startswith("# 합성"):
        reasons.append("합성 보고서 제목 누락")
    if len(markdown) < 2000:
        reasons.append("본문 포함 2,000자 미달")
    if "실제 LLM" not in markdown:
        reasons.append("실제 LLM 미사용 고지 누락")
    for key in DOCUMENTS:
        if f"«{key}»" not in markdown:
            reasons.append(f"알려진 합성 문서 인용 누락: {key}")
    return {"passed": not reasons, "reasons": reasons}
