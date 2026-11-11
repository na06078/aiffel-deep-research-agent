"""공유문맥·소거 실제 그래프 검증; 외부 요청 대역."""

import json
from dataclasses import FrozenInstanceError

import pytest

from 서버.실제연구제어 import RealResearchService
from 서버.실험설정 import ARMS, ExperimentConfig
from 테스트.테스트_실제연구제어 import CORPUS, no_external_requests, settle  # noqa: F401
from 테스트.테스트_실험설정 import ExperimentModel


def test_설정은_불변이며_형식과범위를_검증한다():
    assert len(ARMS) == 8
    with pytest.raises(FrozenInstanceError):
        ARMS["baseline"].section_count = 1
    for kw in ({"section_count": True}, {"reads_per_section": 0}, {"max_rounds": 3}, {"role": 0}):
        with pytest.raises(ValueError, match="experiment:"):
            ExperimentConfig(**kw)


async def test_단일은_12고유문서와_공통메모를_한조사자와_모든절에_전달(tmp_path):
    config = ARMS["single"]
    model = ExperimentModel(config)
    service = RealResearchService(tmp_path, model, CORPUS, experiment_config=config)
    try:
        admitted = service.admit("1894년 질문", "single")
        await settle(service)
        archive = service.repo.archive(admitted["run_id"])
        calls = [e["input"] for e in archive["events"] if e["type"] == "model.started"]
        reads = [c for c in calls if c["context"]["operation"] == "read_one"]
        drafts = [c for c in calls if c["context"]["operation"] == "draft_section"]
        assert len(reads) == 12 and len({c["inputs"]["document"] for c in reads}) == 12
        assert len(drafts) == 4
        assert {c["context"]["actor"] for c in reads + drafts} == {"researcher-1"}
        assert [len(c["inputs"]["shared_notes"]) for c in reads] == list(range(12))
        assert all(len(c["inputs"]["notes"]) == 12 for c in drafts)
        assert all(
            set(c["inputs"]["source_hashes"]) == {r["inputs"]["document"] for r in reads}
            for c in drafts
        )
        assert archive["run"]["status"] == "completed", archive["run"]
        assert archive["run"]["quality"]["passed"] is True
    finally:
        await service.close()


@pytest.mark.parametrize("arm", ["single", "baseline"])
async def test_실험의_명시null은_후보가남으면_고정12읽기를_중단하지않는다(tmp_path, arm):
    class DecliningOnce(ExperimentModel):
        declined = False

        async def complete(self, *args, **kwargs):
            response = await super().complete(*args, **kwargs)
            if kwargs["context"]["operation"] == "select" and not self.declined:
                self.declined = True
                # final02/single의 실제 응답을 보존한 최소 반례다.
                response["content"] = '{"문서":null}'
            return response

    model = DecliningOnce(ARMS[arm])
    service = RealResearchService(tmp_path, model, CORPUS, experiment_config=ARMS[arm])
    try:
        admitted = service.admit("1894년 질문", "single-null")
        await settle(service)
        archive = service.repo.archive(admitted["run_id"])
        starts = [e for e in archive["events"] if e["type"] == "model.started"]
        reads = [e for e in starts if e["input"]["context"]["operation"] == "read_one"]
        assert len(reads) == 12
        if arm == "single":
            assert len({e["input"]["inputs"]["document"] for e in reads}) == 12
        selected = next(e for e in starts if e["input"]["context"]["operation"] == "select")
        completed = next(
            e
            for e in archive["events"]
            if e["type"] == "model.completed"
            and e["output"]["call_id"] == selected["output"]["call_id"]
        )
        assert completed["output"]["content"] == '{"문서":null}'
        fallback = [
            e
            for e in archive["events"]
            if e["type"] == "tool.started"
            and e["input"].get("selection_origin") == "code_budget_fill"
        ]
        assert len(fallback) == 1 and fallback[0]["input"]["model_requested_stop"] is True
        assert fallback[0]["input"]["document"] == selected["input"]["inputs"]["candidates"][0]
        assert archive["run"]["status"] == "completed"
    finally:
        await service.close()


@pytest.mark.parametrize(
    "arm,reads",
    [
        ("baseline", 12),
        ("width2", 6),
        ("depth1", 4),
        ("no-role", 12),
        ("no-region", 12),
        ("no-assignment", 12),
    ],
)
async def test_실제조건별읽기와_배정역할구역_소거(tmp_path, arm, reads):
    config = ARMS[arm]
    model = ExperimentModel(config)
    service = RealResearchService(tmp_path, model, CORPUS, experiment_config=config)
    try:
        admitted = service.admit("전체 질문", arm)
        await settle(service)
        archive = service.repo.archive(admitted["run_id"])
        calls = [e["input"] for e in archive["events"] if e["type"] == "model.started"]
        assert sum(c["context"]["operation"] == "read_one" for c in calls) == reads
        selections = [c for c in calls if c["context"]["operation"] == "select"]
        if arm == "no-region":
            assert all(c["inputs"]["avoid"] == [] for c in selections)
        elif arm != "depth1":
            assert any(c["inputs"]["avoid"] for c in selections)
        if arm == "no-role":
            assert "원인 담당" not in model.calls[0][0]
            assert all(a["role"] == "자료 조사 담당" for a in archive["run"]["plan"])
        if arm == "no-assignment":
            assert all(
                a["seed"] is None and a["instruction"] == "1. 전체 질문"
                for a in archive["run"]["plan"]
            )
            assert all(
                c["inputs"]["assignment"]["title"] == "1. 전체 질문"
                for c in calls
                if c["context"]["operation"] == "draft_section"
            )
        assert archive["run"]["status"] in {"completed", "blocked"}, archive["run"]
    finally:
        await service.close()


async def test_재위임소거는_부족을유지하며_추가조사안함(tmp_path):
    class Insufficient(ExperimentModel):
        async def complete(self, *args, **kwargs):
            response = await super().complete(*args, **kwargs)
            if kwargs["context"]["operation"] == "draft_section":
                obj = json.loads(response["content"])
                obj.update(충분=False, 부족="추가 자료 필요")
                response["content"] = json.dumps(obj, ensure_ascii=False)
            return response

    config = ARMS["no-redelegation"]
    model = Insufficient(config)
    service = RealResearchService(tmp_path, model, CORPUS, experiment_config=config)
    try:
        admitted = service.admit("질문", "no-redelegation")
        await settle(service)
        archive = service.repo.archive(admitted["run_id"])
        sections = [e["output"] for e in archive["events"] if e["type"] == "research.completed"]
        assert len(sections) == 4 and not any(s["충분"] for s in sections)
        assert all(s["round"] == 1 for s in sections)
        assert archive["run"]["research_stop_reason"] == "round_exhausted"
    finally:
        await service.close()
