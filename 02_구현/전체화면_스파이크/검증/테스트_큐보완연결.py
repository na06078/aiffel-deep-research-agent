"""실제 보완 완료의 transaction에서도 명시 재시도 전 큐 정지가 유지되는지 검사한다."""

import json

from 서버.실제연구제어 import RealResearchService
from 테스트.테스트_근거보완연결 import EvidenceModel, seed
from 테스트.테스트_보고서보완연결 import forbid_network, settle  # noqa: F401


async def test_자동보완이_합격해도_커밋직후_기존큐정지는_풀리지않는다(tmp_path):
    corpus = tmp_path / "코퍼스.json"
    corpus.write_text(
        json.dumps(
            {
                "docs": {
                    "자료": "자료에는 두 사건의 기록이 각각 남아 있다. 세 사건을 구별할 추가 기록이 보존되어 있다."
                },
                "links": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = RealResearchService(tmp_path / "저널", EvidenceModel(), corpus)
    state = seed(service)
    with service.repo.transaction() as (db, data):
        run = service.repo.run(data, state["run_id"])
        run["retry_queue_pause"] = {"reason": "cancelled"}
        data.update(queue_paused=True, queue_pause_reason="cancelled")
        data["queue"] = [
            {
                "instruction_id": "keep-queued",
                "text": "보존할 대기 지시",
                "status": "queued",
                "source_mode": "local",
            }
        ]
        service.repo.save_revision(db, run)
    committed = []
    # Emulate a crash immediately before start_next_queue can repair the saved state.
    service.start_next_queue = lambda: committed.append(service.repo.snapshot())
    try:
        await service.evaluate(state)
        await settle(service)
        final = service.repo.snapshot()
        assert any(r["status"] == "completed" for r in final["runs"])
        assert committed, "successful correction did not reach post-commit boundary"
        assert committed[-1]["queue_paused"] is True
        assert committed[-1]["queue_pause_reason"] == "cancelled"
        assert [q["instruction_id"] for q in committed[-1]["queue"]] == ["keep-queued"]
    finally:
        await service.close()
