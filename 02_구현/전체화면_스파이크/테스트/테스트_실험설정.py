"""실험 제어는 실제 기획/읽기/공유문맥 경계에서 관측한다."""

import json

import pytest

from 서버.실제연구제어 import RealResearchService
from 서버.실험설정 import ARMS
from 테스트.테스트_실제연구제어 import CORPUS, ScriptedModel, no_external_requests, settle  # noqa: F401


class ExperimentModel(ScriptedModel):
    def __init__(self, config):
        super().__init__()
        self.config = config

    async def complete(self, *args, **kwargs):
        response = await super().complete(*args, **kwargs)
        operation = kwargs["context"]["operation"]
        if operation == "plan":
            obj = json.loads(response["content"])
            obj["목차"] = obj["목차"][: self.config.section_count]
            if not self.config.role:
                for a in obj["목차"]:
                    a["역할"] = "자료 조사 담당"
            response["content"] = json.dumps(obj, ensure_ascii=False)
        elif operation == "select":
            candidates = args[1].split("[후보] ", 1)[1].split("\n", 1)[0].split(", ")
            response["content"] = json.dumps({"문서": candidates[0]}, ensure_ascii=False)
        elif operation == "draft_section":
            obj = json.loads(response["content"])
            obj.update(충분=True, 부족="")
            response["content"] = json.dumps(obj, ensure_ascii=False)
        return response


async def test_폭조건은_실제기획을_두절로_제한한다(tmp_path):
    config = ARMS["width2"]
    model = ExperimentModel(config)
    service = RealResearchService(tmp_path, model, CORPUS, experiment_config=config)
    with service.repo.transaction() as (db, data):
        run, _ = service.start(db, data, "질문", "width")
    try:
        result = await service.plan({"run_id": run["run_id"], "revision": 1})
        assert len(result["plan"]) == 2
        assert sum(a["budget"] for a in result["plan"]) == 6
    finally:
        await service.close()


async def test_설정과다른_기획은_절단하지않고_거절(tmp_path):
    from 서버.교안연구 import ResearchContractError

    service = RealResearchService(
        tmp_path, ScriptedModel(), CORPUS, experiment_config=ARMS["width2"]
    )
    with service.repo.transaction() as (db, data):
        run, _ = service.start(db, data, "질문", "invalid")
    try:
        with pytest.raises(ResearchContractError, match="section count mismatch"):
            await service.plan({"run_id": run["run_id"], "revision": 1})
        assert service.repo.snapshot()["runs"][0]["plan"] == []
    finally:
        await service.close()


def test_앱factory가_검증된설정을_그대로연결(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from 서버 import 연구앱

    config = ARMS["single"]
    monkeypatch.setattr(연구앱, "ROOT", tmp_path)
    monkeypatch.setattr(
        연구앱, "create_app", lambda **kw: SimpleNamespace(factory=kw["service_factory"])
    )
    seen = {}
    monkeypatch.setattr(연구앱, "RealResearchService", lambda *args, **kwargs: seen.update(kwargs))
    app = 연구앱.create_research_app(
        data_dir=tmp_path / "data", model=object(), web_client=object(), experiment_config=config
    )
    app.factory(tmp_path / "data")
    assert seen["experiment_config"] is config
    with pytest.raises(ValueError, match="validated ExperimentConfig"):
        연구앱.create_research_app(model=object(), experiment_config={"arm": "single"})
