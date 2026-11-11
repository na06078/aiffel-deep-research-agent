"""실제 보완 감독자→evaluate→검토 입력의 회귀. 실제 모델/네트워크 없음."""

import json

import pytest

from 서버.실제연구제어 import RealResearchService
from 테스트.테스트_근거보완연결 import EvidenceModel, seed
from 테스트.테스트_보고서보완연결 import forbid_network, settle  # noqa: F401


async def test_자동보완의_실제원본과변경을_최종검토까지_전달(tmp_path):
    corpus = tmp_path / "합성코퍼스.json"
    corpus.write_text(
        json.dumps(
            {
                "docs": {
                    "자료": "자료에는 두 사건의 기록이 각각 남아 있다. 세 사건을 구별할 추가 기록이 보존되어 있다."
                },
                "links": {},
            }
        ),
        encoding="utf-8",
    )
    model = EvidenceModel()
    service = RealResearchService(tmp_path / "저널", model, corpus)
    try:
        state = seed(service)
        await service.evaluate(state)
        await settle(service)
        archive = service.repo.archive(state["run_id"])
        reviewed = json.loads(model.calls[-1]["user"])
        applied = next(
            e["output"] for e in archive["events"] if e["type"] == "report.correction.applied"
        )
        assert reviewed.get("correction_scope") == {
            "source_revision": 1,
            "original_draft": state["draft"],
            "changes": [
                {k: c[k] for k in ("target_id", "section_id", "before", "after")}
                for c in applied["changes"]
            ],
        }
        assert [c["context"]["operation"] for c in model.calls] == [
            "semantic_review",
            "semantic_correction",
            "semantic_review",
        ]
        assert archive["run"]["report_id"]
    finally:
        await service.close()


@pytest.mark.parametrize("automatic", [True, False])
async def test_실제재검토_scope음성은_정본출판을차단한다(tmp_path, automatic):
    from 테스트.테스트_보고서보완연결 import CorrectionModel, seed_blocked

    class ScopeNegative(EvidenceModel if automatic else CorrectionModel):
        async def complete(self, system, user, **kwargs):
            response = await super().complete(system, user, **kwargs)
            if (
                kwargs["context"]["operation"] == "semantic_review"
                and kwargs["context"]["revision"] == 2
            ):
                payload = json.loads(user)
                scope = payload.get("correction_scope")
                assert scope and scope["source_revision"] == 1
                response["content"] = json.dumps(
                    {
                        "검토목록ID": payload["review_set_id"],
                        "판정": "needs_revision",
                        "문제": [
                            {
                                "유형": "scope_expansion",
                                "설명": "허용 범위를 넘긴 사실 추가라는 명시 대역 판정",
                                "본문발췌": scope["changes"][0]["after"],
                                "근거번호": [],
                            }
                        ],
                        "검토요약": "범위 부정의 상태/전송 검증용 대역",
                    },
                    ensure_ascii=False,
                )
            return response

    corpus = tmp_path / "범위코퍼스.json"
    corpus.write_text(
        json.dumps(
            {
                "docs": {
                    "자료": "자료에는 두 사건의 기록이 각각 남아 있다. 세 사건을 구별할 추가 기록이 보존되어 있다."
                },
                "links": {},
            }
        ),
        encoding="utf-8",
    )
    model = ScopeNegative()
    service = RealResearchService(tmp_path / "범위저널", model, corpus)
    try:
        if automatic:
            state = seed(service)
            await service.evaluate(state)
            run_id = state["run_id"]
        else:
            original, _ = seed_blocked(service)
            run_id = original["run_id"]
            service.request_correction(run_id, 1, "manual-scope")
        await settle(service)
        archive = service.repo.archive(run_id)
        run = archive["run"]
        assert run["status"] == "blocked" and run["report_id"] is None
        assert run["quality"]["reasons"] == ["semantic_review_failed"]
        assert run["quality"]["semantic_review"]["문제"][0]["유형"] == "scope_expansion"
        assert not any(e["type"] == "report.submitted" for e in archive["events"])
        assert len(model.calls) == (3 if automatic else 2)
        assert run["correction_state"]["passed"] is False
        review = json.loads(model.calls[-1]["user"])
        assert review["correction_scope"]["original_draft"] == archive["revisions"][0]["draft"]
    finally:
        await service.close()
