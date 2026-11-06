"""실제 코퍼스·제공자와 무관한 명시적 비동기 모델 대역."""

import asyncio


class FakeModel:
    async def plan(self, payload):
        instruction = payload["instruction"]
        await asyncio.sleep(0)
        return [
            {
                "section_id": f"section-{i}",
                "title": f"합성 시험 {i}절: {instruction}",
                "assignee": f"researcher-{i}",
            }
            for i in range(1, 5)
        ]

    async def research(self, gate, payload):
        instruction, assignment = payload["instruction"], payload["assignment"]
        outcome = await gate
        if outcome == "failure":
            raise RuntimeError("의도적으로 발생시킨 합성 모델 오류")
        return {
            "synthetic": True,
            "instruction": instruction,
            "section_id": assignment["section_id"],
            "outcome": outcome,
            "text": f"실제 LLM 아님. 합성 조사: {instruction} / {assignment['title']}",
            "document": payload["document_id"],
            "source": payload["source"],
        }

    async def synthesize(self, payload):
        instruction, results = payload["instruction"], payload["results"]
        await asyncio.sleep(0)
        if any(r["outcome"] == "quality_fail" for r in results):
            return "# 불합격 합성 초안\n길이와 알려진 문서 인용이 빠진 실제 불합격 후보."
        body = f"# 합성 실행 제어 시험 보고서\n\n실제 LLM 미사용: 테스트 대역의 합성 결과입니다.\n\n현재 지시: {instruction}\n"
        for i, result in enumerate(sorted(results, key=lambda r: r["section_id"]), 1):
            body += f"\n## {i}. 합성 검증 절\n{result['text']}\n"
            for _ in range(4):
                body += f"\n{result['source']} «{result['document']}»\n"
        return body
