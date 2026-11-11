"""삭제 수명 경합: 실제 감독자·저널, 지연 제공자 대역, 임시 데이터만 사용한다."""

import asyncio
import json

import pytest

from 서버.교안연구 import ResearchContractError
from 서버.보고서변환 import ReportExports
from 서버.실제연구제어 import RealResearchService, SupersededResearch
from 서버.실행삭제 import RunDeletion
from 서버.실행제어 import ExecutionService
from 서버.저장소 import Repository
from 서버.활동기록 import Activity
from 테스트.테스트_실제연구제어 import CORPUS, ScriptedModel, settle
from 테스트.테스트_실행삭제 import seed_web, web_counts
from 테스트.테스트_웹연구 import FakeWebClient, WebModel


class GatedModel:
    def __init__(self, outcome="success"):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.outcome = outcome

    async def synthesize(self, payload):
        self.entered.set()
        await self.release.wait()
        if self.outcome == "error":
            raise RuntimeError("지연 대역 실패")
        return "삭제 대상 비공개 초안"

    async def complete(self, system, user, *, on_attempt, response_schema=None, **kwargs):
        on_attempt("gated-attempt")
        content = await self.synthesize({})
        return {"content": content, "attempt_id": "gated-attempt"}

    async def aclose(self):
        pass


def real_service(path, model, **kwargs):
    path.mkdir(parents=True, exist_ok=True)
    corpus = path / "시험코퍼스.json"
    corpus.write_text(json.dumps({"docs": {"자료": "대역 본문"}, "links": {}}), encoding="utf-8")
    return RealResearchService(path, model, corpus, **kwargs)


def start_without_graph(service, text="삭제 대상 비공개 질문"):
    with service.repo.transaction() as (db, data):
        run, _ = service.start(db, data, text, "fixture")
    return {"run_id": run["run_id"], "revision": 1, "instruction": text, "results": []}


def assert_absent(repo, run_id):
    with repo.read_connection() as db:
        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[
                0
            ]
            for table in ("calls", "revisions", "activity_spans")
        }
        counts.update(
            {
                table: db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE json_extract(body,'$.run_id')=?", (run_id,)
                ).fetchone()[0]
                for table in ("events", "reports")
            }
        )
        requests = db.execute("SELECT body,response FROM requests").fetchall()
        assert len(requests) == 1
        assert json.loads(requests[0][0]) == {"action": "delete", "run_id": run_id}
        assert json.loads(requests[0][1])["status"] == "deleted"
    assert counts == dict.fromkeys(counts, 0), counts
    assert repo.run(repo.snapshot(), run_id) is None
    assert not list(repo.root.glob("*.md"))


@pytest.mark.parametrize("source_mode", ["local", "web"])
async def test_구간초안대기중_삭제는_늦은원응답과_조립결과를_복원하지_않는다(tmp_path, source_mode):
    base = ScriptedModel if source_mode == "local" else WebModel

    class DelayedDraft(base):
        def __init__(self):
            super().__init__()
            self.entered, self.release = asyncio.Event(), asyncio.Event()

        async def complete(self, *args, **kwargs):
            response = await super().complete(*args, **kwargs)
            if kwargs["context"]["operation"] == "draft_section":
                catalog = json.loads(args[1])
                assert catalog["evidence"]  # 실제 읽기·구간 생성 양성 대조.
                assert kwargs["response_schema"]["properties"]["근거목록ID"]["enum"] == [
                    catalog["evidence_set_id"]
                ]
                raw = json.loads(response["content"])
                assert raw["근거목록ID"] == catalog["evidence_set_id"]
                assert all(type(s) is dict and set(s) == {"내용", "근거구간"} for s in raw["문장"])
                assert raw["문장"][0]["근거구간"] == catalog["evidence"][0]["id"]
                assert kwargs["response_schema"]["properties"]["문장"]["maxItems"] == 16
                self.entered.set()
                await self.release.wait()
            return response

    model = DelayedDraft()
    service = RealResearchService(tmp_path, model, CORPUS, web_client=FakeWebClient())
    try:
        admitted = service.admit("/웹 자료" if source_mode == "web" else "자료", "first")
        run_id = admitted["run_id"]
        await asyncio.wait_for(model.entered.wait(), 3)
        count = len(model.calls)
        service.cancel(run_id, 1, "cancel")
        assert (
            RunDeletion(service, ReportExports(service)).delete(run_id, run_id, "delete")["status"]
            == "deleted"
        )
        assert_absent(service.repo, run_id)
        model.release.set()
        await settle(service)
        assert_absent(service.repo, run_id)
        assert len(model.calls) == count
        assert service.web_store.sources(run_id, 1) == []
        assert service.web_store.read_claims(run_id, 1) == []
    finally:
        model.release.set()
        await service.close()


async def test_실제_synthesize_취소삭제후_지연성공과_finally가_행을_복원하지_않는다(tmp_path):
    service = ExecutionService(tmp_path)
    model = service.model = GatedModel()
    state = start_without_graph(service)
    task = asyncio.create_task(service.synthesize(state))
    await asyncio.wait_for(model.entered.wait(), 2)
    service.cancel(state["run_id"], 1, "cancel")
    other = start_without_graph(service, "다른 활성 실행")
    before = service.repo.archive(other["run_id"])
    result = RunDeletion(service, ReportExports(service)).delete(
        state["run_id"], state["run_id"], "delete"
    )
    assert result["status"] == "deleted" and not result["warnings"]
    assert_absent(service.repo, state["run_id"])
    model.release.set()
    await task
    assert_absent(service.repo, state["run_id"])
    after = service.repo.archive(other["run_id"])
    for key in ("run", "events", "spans", "revisions"):
        assert after[key] == before[key]
    assert after["run"]["status"] == "running"


@pytest.mark.parametrize("real", [False, True], ids=["synthesize", "real-complete-plan"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
@pytest.mark.parametrize("disposition", ["deleted", "cancelled", "revised"])
async def test_늦은응답_실패_취소종료는_삭제만_차단하고_비삭제_기록을_보존한다(
    tmp_path, real, outcome, disposition
):
    model = GatedModel(outcome)
    service = real_service(tmp_path, model) if real else ExecutionService(tmp_path)
    service.model = model
    state = start_without_graph(service)
    task = asyncio.create_task(service.plan(state) if real else service.synthesize(state))
    await asyncio.wait_for(model.entered.wait(), 2)
    run_id = state["run_id"]
    if disposition == "revised":
        revised = start_without_graph(service, "새 버전 지시")
        assert revised["run_id"] == run_id
        assert service.repo.snapshot()["runs"][0]["control_revision"] == 2
    else:
        service.cancel(run_id, 1, "cancel")
    if disposition == "deleted":
        other = start_without_graph(service, "늦은 종료와 무관한 활성 실행")
        before_other = service.repo.archive(other["run_id"])
        before_state = service.repo.snapshot()
        assert (
            RunDeletion(service, ReportExports(service)).delete(run_id, run_id, "delete")["status"]
            == "deleted"
        )
        assert_absent(service.repo, run_id)
    if outcome == "cancel":
        task.cancel()
    else:
        model.release.set()
    result = (await asyncio.gather(task, return_exceptions=True))[0]
    if outcome == "cancel":
        assert isinstance(result, asyncio.CancelledError)
    elif outcome == "error":
        assert isinstance(result, ResearchContractError if real else RuntimeError)
    elif real:
        assert isinstance(result, SupersededResearch)
    else:
        assert result == {"draft": "삭제 대상 비공개 초안"}
    if disposition == "deleted":
        assert_absent(service.repo, run_id)
        # 재시작 뒤에도 새 Repository/Activity에서 영구 흔적을 존중한다.
        repo = Repository(tmp_path, provider=service.repo.provider, mode=service.repo.mode)
        with repo.transaction() as (db, _):
            assert (
                repo.event(db, run_id, 1, "result.superseded", "editor", output="민감 본문") is None
            )
        span = Activity(run_id, 1, "editor", "editor", "late", "node")
        with repo.connection() as db:
            span.finish(db, "completed")
            db.commit()
        assert_absent(repo, run_id)
        after_other = repo.archive(other["run_id"])
        for key in ("run", "events", "spans", "revisions"):
            assert after_other[key] == before_other[key]
        after_state = repo.snapshot()
        for key in ("queue", "queue_paused", "queue_pause_reason", "selected_source_mode"):
            assert after_state[key] == before_state[key]
    else:
        archive = service.repo.archive(run_id)
        assert len(archive["spans"]) == 2
        assert all(s["ended_at"] for s in archive["spans"])
        terminal = [e for e in archive["events"] if e["type"] == "result.superseded"]
        if outcome == "success" or (real and outcome == "error"):
            assert len(terminal) == 1
            assert terminal[0]["input"]
        if outcome == "success":
            assert "삭제 대상 비공개 초안" in json.dumps(terminal[0], ensure_ascii=False)


@pytest.mark.parametrize("operation", ["search", "fetch"])
@pytest.mark.parametrize("observed", [False, True])
@pytest.mark.parametrize("fail", [False, True])
async def test_지연_웹검색과_수집_응답_실패_observer는_삭제후_자료를_복원하지_않는다(
    tmp_path, operation, observed, fail
):
    from 서버.웹계약 import WebSourceError

    held = asyncio.Event()

    class DelayedWeb(FakeWebClient):
        async def emit(self, observer, op, url):
            if op == operation:
                held.set()
                await self.release.wait()
                if observed:
                    # 실제 observer는 worker thread에서도 저널 펜스를 먼저 확인한다.
                    await asyncio.to_thread(
                        observer, "request.completed", {"web_request_id": "late"}
                    )
                if fail:
                    raise WebSourceError("fetch_failed" if op == "fetch" else "search_failed")
            elif observed:
                await super().emit(observer, op, url)

    web = DelayedWeb()
    service = real_service(tmp_path, WebModel(), web_client=web)
    state = start_without_graph(service)
    state.update(
        source_mode="web",
        prior={},
        assignment={
            "assignee": "researcher-1",
            "section_id": "section-1",
            "title": "자료 범위",
            "instruction": "범위 확인",
            "role": "큰그림 담당",
            "queries": ["공개 자료"],
            "round": 1,
            "order": 0,
        },
    )
    task = asyncio.create_task(service.research(state))
    await asyncio.wait_for(held.wait(), 2)
    assert web.calls[-1][0] == operation
    service.cancel(state["run_id"], 1, "cancel")
    other = start_without_graph(service, "보존할 활성 실행")
    store = seed_web(tmp_path, state["run_id"], other["run_id"])
    target_ids = [s["source_id"] for s in store.sources(state["run_id"], 1)]
    keep_sources = store.sources(other["run_id"], 1)
    keep_reads = store.read_claims(other["run_id"], 1)
    before = service.repo.archive(other["run_id"])
    assert (
        RunDeletion(service, ReportExports(service)).delete(
            state["run_id"], state["run_id"], "delete"
        )["warnings"]
        == []
    )
    web.release.set()
    result = (await asyncio.gather(task, return_exceptions=True))[0]
    assert isinstance(result, SupersededResearch)
    assert_absent(service.repo, state["run_id"])
    assert web_counts(store.path, state["run_id"], target_ids) == {
        "web_sources": 0,
        "web_reads": 0,
        "web_history": 0,
    }
    assert store.sources(other["run_id"], 1) == keep_sources
    assert store.read_claims(other["run_id"], 1) == keep_reads
    assert web_counts(store.path, other["run_id"])["web_history"] == 1
    after = service.repo.archive(other["run_id"])
    for key in ("run", "events", "spans", "revisions"):
        assert after[key] == before[key]
