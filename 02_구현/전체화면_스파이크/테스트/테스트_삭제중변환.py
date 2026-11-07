"""실제 삭제/변환 소유자 경합. 렌더러는 gate 대역이며 브라우저를 실행하지 않는다."""

import asyncio

import pytest

from 서버.보고서변환 import ReportExports
from 서버.실행삭제 import RunDeletion
from 테스트.테스트_실행삭제 import deletion_client, finish, run_of
from 테스트.테스트_실행제어 import snapshot


@pytest.mark.parametrize("format", ["html", "pdf"])
@pytest.mark.parametrize("outcome", ["success", "error", "cancel", "timeout"])
async def test_삭제중_변환종료가_본문과_메타데이터를_복원하지_않는다(tmp_path, format, outcome):
    entered, release = asyncio.Event(), asyncio.Event()

    async def renderer(content, format, report):
        if report["report_id"] == keep_report["report_id"]:
            return b"keep-rendered-content"
        entered.set()
        await release.wait()
        if outcome == "error":
            raise RuntimeError("대역 렌더 실패")
        if outcome == "timeout":
            raise TimeoutError("대역 렌더 제한시간")
        return b"private-rendered-content"

    async with deletion_client(tmp_path) as (client, app):
        keep = await finish(client, app, "보존할 보고서", "keep")
        keep_report = app.state.service.report_bytes(
            run_of(await snapshot(client), keep)["report_id"]
        )[0]
        target = await finish(client, app, "삭제할 보고서", "start")
        service = app.state.service
        report_id = run_of(await snapshot(client), target)["report_id"]
        report = service.report_bytes(report_id)[0]
        exports = ReportExports(service, renderer)
        exports.request(report_id, format, report["sha256"])
        tasks = list(exports.tasks.values())
        await asyncio.wait_for(entered.wait(), 2)
        result = RunDeletion(service, exports).delete(target, target, "delete")
        assert result["status"] == "deleted" and not result["warnings"]
        assert exports.files_of({report_id}) == []
        if outcome == "cancel":
            tasks[0].cancel()
        else:
            release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if outcome == "cancel":
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert results == [None]
        assert list(exports.root.glob("*")) == []
        # 새 소유자도 삭제된 정본의 메타데이터를 재생성하지 않는다.
        restarted = ReportExports(service, renderer)
        restarted.save(report, restarted.empty(report, format))
        assert list(exports.root.glob("*")) == []
        restarted.request(keep_report["report_id"], format, keep_report["sha256"])
        await asyncio.gather(*list(restarted.tasks.values()))
        ready = restarted.entry(keep_report, format)
        assert ready["status"] == "ready"
        assert restarted.verified_bytes(keep_report, ready) == b"keep-rendered-content"
        assert len(list(exports.root.glob("*"))) == 2


@pytest.mark.parametrize("cancel_queued", [False, True])
async def test_PDF슬롯에서_대기하던_삭제대상은_성공과_취소에도_파일을_남기지_않는다(
    tmp_path, cancel_queued
):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def renderer(content, format, report):
        calls.append(report["report_id"])
        entered.set()
        await release.wait()
        return b"queued-renderer"

    async with deletion_client(tmp_path) as (client, app):
        keep = await finish(client, app, "슬롯 소유 보고서", "keep")
        target = await finish(client, app, "대기 보고서", "target")
        service = app.state.service
        state = await snapshot(client)
        keep_report = service.report_bytes(run_of(state, keep)["report_id"])[0]
        target_report = service.report_bytes(run_of(state, target)["report_id"])[0]
        exports = ReportExports(service, renderer)
        exports.request(keep_report["report_id"], "pdf", keep_report["sha256"])
        await asyncio.wait_for(entered.wait(), 2)
        exports.request(target_report["report_id"], "pdf", target_report["sha256"])
        queued = exports.tasks[exports.key(target_report, "pdf")]
        await asyncio.sleep(0)
        assert calls == [keep_report["report_id"]] and not queued.done()
        assert RunDeletion(service, exports).delete(target, target, "delete")["warnings"] == []
        if cancel_queued:
            queued.cancel()
        release.set()
        results = await asyncio.gather(*list(exports.tasks.values()), return_exceptions=True)
        assert all(r is None or isinstance(r, asyncio.CancelledError) for r in results)
        assert not exports.files_of({target_report["report_id"]})
        assert all(
            not exports.path(target_report, "pdf", suffix).exists() for suffix in ("json", "pdf")
        )
        assert len(exports.files_of({keep_report["report_id"]})) == 2
        assert exports.entry(keep_report, "pdf")["status"] == "ready"
