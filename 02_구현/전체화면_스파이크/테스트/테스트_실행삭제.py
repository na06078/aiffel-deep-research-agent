"""실행 영구 삭제(명세 28.2~28.4)의 서버 계약. 임시 폴더·대역 모델만 쓴다."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from 서버.실행제어 import ExecutionService
from 서버.앱 import create_app
from 서버.웹자료저장소 import WebSourceStore
from 서버.호출예산 import CallBudget
from 테스트.테스트_실행제어 import command, release, snapshot, until

COUNT_KEYS = {
    "events",
    "calls",
    "activity_spans",
    "requests",
    "reports",
    "report_files",
    "export_files",
    "web_sources",
    "web_reads",
}


class WebDoubleService(ExecutionService):
    """실제 연구 서비스처럼 web_store를 지연 생성하는 대역(웹 자료 DB 정리 경로용)."""

    @property
    def web_store(self):
        if getattr(self, "_web_store", None) is None:
            self._web_store = WebSourceStore(self.repo.root / "웹자료/자료.sqlite3")
        return self._web_store


@asynccontextmanager
async def deletion_client(path, service_factory=None):
    app = create_app(data_dir=path, service_factory=service_factory)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        yield client, app


async def start(client, text, request_id):
    response = await command(client, text, request_id)
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    await until(client, lambda s: len(s["waiting"]) >= 4)
    return run_id


async def finish(client, app, text, request_id):
    """합성 지시 하나를 끝까지 진행해 보고서가 있는 완료 실행을 만든다."""
    run_id = await start(client, text, request_id)
    state = await snapshot(client)
    await release(client, [g for g in state["waiting"] if not g["is_stale"]])
    await until(
        client,
        lambda s: next(r for r in s["runs"] if r["run_id"] == run_id)["status"] == "completed",
    )
    await asyncio.gather(*list(app.state.service.tasks))
    return run_id


def run_of(state, run_id):
    return next((r for r in state["runs"] if r["run_id"] == run_id), None)


def delete_body(run_id, confirm=None, request_id="del-1"):
    return {"client_request_id": request_id, "confirm_run_id": confirm or run_id}


async def delete(client, run_id, **kwargs):
    return await client.post(f"/api/spike/runs/{run_id}/delete", json=delete_body(run_id, **kwargs))


def sql(app, query, *args):
    with sqlite3.connect(app.state.service.repo.path) as db:
        return db.execute(query, args).fetchall()


def rows_of_run(app, run_id):
    """삭제 대상 테이블에 이 실행이 남긴 행 수(요청은 본문·응답 모두 본다)."""
    q = {
        "revisions": "SELECT COUNT(*) FROM revisions WHERE run_id=?",
        "calls": "SELECT COUNT(*) FROM calls WHERE run_id=?",
        "activity_spans": "SELECT COUNT(*) FROM activity_spans WHERE run_id=?",
        "events": "SELECT COUNT(*) FROM events WHERE json_extract(body,'$.run_id')=?",
        "reports": "SELECT COUNT(*) FROM reports WHERE json_extract(body,'$.run_id')=?",
        "requests": "SELECT COUNT(*) FROM requests WHERE json_extract(body,'$.run_id')=?"
        " OR json_extract(response,'$.run_id')=?",
    }
    return {
        name: sql(app, query, *([run_id] * query.count("?")))[0][0] for name, query in q.items()
    }


def seed_exports(app, report_id):
    """보고서에 HTML·PDF 변환 항목을 만든다(항목 json 2 + 본문 2 = 파일 4개)."""
    exports = app.state.exports
    report = app.state.service.report_bytes(report_id)[0]
    for fmt in ("html", "pdf"):
        entry = exports.empty(report, fmt) | {"status": "ready"}
        exports.save(report, entry)
        exports.atomic(exports.path(report, fmt, fmt), b"fixture-" + fmt.encode())
    return [
        exports.path(report, fmt, suffix) for fmt in ("html", "pdf") for suffix in (fmt, "json")
    ]


def seed_web(root, run_id, other_run_id):
    """대상 실행과 다른 실행의 웹 자료를 같은 DB에 넣는다. 대상: 출처 2·읽음 1."""
    store = WebSourceStore(root / "웹자료/자료.sqlite3")
    hit = lambda n: {"title": "t", "url": f"https://example.com/{n}", "snippet": "s"}
    for owner, urls in ((run_id, (1, 2)), (other_run_id, (3,))):
        for source in store.register_candidates(owner, 1, [hit(n) for n in urls]):
            text = "본문 " + source["url"]
            store.save_fetch(
                owner,
                1,
                source["source_id"],
                {
                    "requested_url": source["url"],
                    "final_url": source["url"],
                    "title": "원문",
                    "text": text,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "html_sha256": "a" * 64,
                    "fetched_at": "2026-01-01T00:00:00+00:00",
                    "extracted_date_candidate": None,
                    "warnings": [],
                },
            )
            if owner == run_id and source["url"].endswith("/1"):
                store.mark_read(owner, 1, source["source_id"], "s1", "call-1", text, "메모")
            if owner == other_run_id:
                store.mark_read(owner, 1, source["source_id"], "s1", "call-2", text, "메모")
    return store


def web_counts(path, run_id, source_ids=None):
    with sqlite3.connect(path) as db:
        # 삭제 후의 빈 web_sources로 고아 이력을 놓치지 않도록 삭제 전 ID를 받는다.
        if source_ids is None:
            source_ids = [
                row[0]
                for row in db.execute("SELECT source_id FROM web_sources WHERE run_id=?", (run_id,))
            ]
        placeholders = ",".join("?" for _ in source_ids)
        return {
            "web_sources": db.execute(
                "SELECT COUNT(*) FROM web_sources WHERE run_id=?", (run_id,)
            ).fetchone()[0],
            "web_reads": db.execute(
                "SELECT COUNT(*) FROM web_reads WHERE run_id=?", (run_id,)
            ).fetchone()[0],
            "web_history": db.execute(
                f"SELECT COUNT(*) FROM web_history WHERE source_id IN ({placeholders})",
                source_ids,
            ).fetchone()[0],
        }


# ---------------------------------------------------------------- preview


async def test_preview_개수는_실제_행과_파일과_일치하고_아무것도_바꾸지_않는다(tmp_path):
    async with deletion_client(tmp_path, WebDoubleService) as (client, app):
        run_id = await finish(client, app, "삭제 대상 합성 질문", "r1")
        other = await finish(client, app, "남길 합성 질문", "r2")
        report_id = run_of(await snapshot(client), run_id)["report_id"]
        seed_exports(app, report_id)
        seed_web(tmp_path, run_id, other)
        before_db = app.state.service.repo.path.read_bytes()
        before_web = (tmp_path / "웹자료/자료.sqlite3").read_bytes()

        response = await client.get(f"/api/spike/runs/{run_id}/delete-preview")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"] == run_id
        assert set(body["counts"]) == COUNT_KEYS
        rows = rows_of_run(app, run_id)
        assert body["counts"]["events"] == rows["events"] > 0
        assert body["counts"]["calls"] == rows["calls"] > 0
        assert body["counts"]["activity_spans"] == rows["activity_spans"]
        assert body["counts"]["requests"] == rows["requests"] > 0
        assert body["counts"]["reports"] == rows["reports"] == 1
        assert body["counts"]["report_files"] == 1
        assert body["counts"]["export_files"] == 4
        assert body["counts"]["web_sources"] == 2
        assert body["counts"]["web_reads"] == 1
        # 다음 실행(other)이 이 보고서를 문맥으로 쓰므로 막힌다.
        assert body["deletable"] is False
        assert [b["code"] for b in body["blockers"]] == ["has_dependents"]
        assert app.state.service.repo.path.read_bytes() == before_db
        assert (tmp_path / "웹자료/자료.sqlite3").read_bytes() == before_web


async def test_preview는_없는_실행에_404를_준다(tmp_path):
    async with deletion_client(tmp_path) as (client, _app):
        response = await client.get("/api/spike/runs/없는실행/delete-preview")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_preview에서_웹자료_DB가_없으면_0이고_파일을_만들지_않는다(tmp_path):
    async with deletion_client(tmp_path, WebDoubleService) as (client, app):
        run_id = await finish(client, app, "웹 DB 없음", "r1")
        body = (await client.get(f"/api/spike/runs/{run_id}/delete-preview")).json()
        assert body["counts"]["web_sources"] == body["counts"]["web_reads"] == 0
        assert body["deletable"] is True and body["blockers"] == []
        assert not (tmp_path / "웹자료").exists()


async def test_preview는_진행중_실행을_run_running으로_막는다(tmp_path):
    async with deletion_client(tmp_path) as (client, _app):
        run_id = await start(client, "진행 중 합성 질문", "r1")
        body = (await client.get(f"/api/spike/runs/{run_id}/delete-preview")).json()
        assert body["deletable"] is False
        assert [b["code"] for b in body["blockers"]] == ["run_running"]
        assert all(b["message"] for b in body["blockers"])


async def test_preview의_dependents는_후속_실행의_ID_질문_생성시각을_담는다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        first = await finish(client, app, "첫 합성 질문", "r1")
        second = await finish(client, app, "둘째 합성 질문", "r2")
        body = (await client.get(f"/api/spike/runs/{first}/delete-preview")).json()
        blocker = body["blockers"][0]
        assert blocker["code"] == "has_dependents"
        child = run_of(await snapshot(client), second)
        assert blocker["dependents"] == [
            {"run_id": second, "question": child["question"], "created_at": child["created_at"]}
        ]


# ---------------------------------------------------------------- 연쇄 삭제


async def test_삭제는_DB_상태_보고서_변환_웹자료를_모두_지우고_예산_장부는_그대로다(tmp_path):
    ledger = CallBudget(tmp_path / "호출예산.sqlite3").path
    ledger_before = ledger.read_bytes()
    async with deletion_client(tmp_path, WebDoubleService) as (client, app):
        keep = await finish(client, app, "남길 합성 질문", "r1")
        target = await finish(client, app, "지울 합성 질문", "r2")
        state = await snapshot(client)
        report_id = run_of(state, target)["report_id"]
        keep_report = run_of(state, keep)["report_id"]
        md = tmp_path / app.state.service.report_bytes(report_id)[2]
        keep_md = tmp_path / app.state.service.report_bytes(keep_report)[2]
        assert md.exists()
        exports = seed_exports(app, report_id)
        keep_exports = seed_exports(app, keep_report)
        store = seed_web(tmp_path, target, keep)
        target_sources = [s["source_id"] for s in store.sources(target, 1)]
        assert len(target_sources) == 2
        assert web_counts(store.path, target, target_sources)["web_history"] == 2
        preview = (await client.get(f"/api/spike/runs/{target}/delete-preview")).json()
        keep_rows = rows_of_run(app, keep)
        keep_run = run_of(state, keep)

        response = await delete(client, target)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "deleted" and body["run_id"] == target
        assert body["warnings"] == []
        assert body["counts"] == preview["counts"]
        assert body["current_run_id"] == keep
        assert set(body) == {"status", "run_id", "counts", "warnings", "current_run_id"}
        gone = rows_of_run(app, target)
        # 삭제 요청 자체의 멱등 기록(본문 run_id)만 남을 수 있다.
        assert gone | {"requests": 0} == dict.fromkeys(gone, 0)
        assert run_of(await snapshot(client), target) is None
        assert not md.exists() and not any(p.exists() for p in exports)
        assert keep_md.exists() and all(p.exists() for p in keep_exports)
        assert web_counts(
            tmp_path / "웹자료/자료.sqlite3", target, target_sources
        ) == dict.fromkeys(("web_sources", "web_reads", "web_history"), 0)
        assert web_counts(tmp_path / "웹자료/자료.sqlite3", keep)["web_sources"] == 1
        assert rows_of_run(app, keep) == keep_rows
        assert run_of(await snapshot(client), keep) == keep_run
    assert ledger.read_bytes() == ledger_before


async def test_삭제_뒤_archive_event_report는_404다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        await finish(client, app, "남길", "r1")
        target = await finish(client, app, "지울", "r2")
        archive = (await client.get(f"/api/spike/runs/{target}/archive")).json()
        event_id = archive["events"][0]["event_id"]
        report_id = archive["run"]["report_id"]
        assert (await client.get(f"/api/spike/events/{event_id}")).status_code == 200
        assert (await delete(client, target)).status_code == 200
        for url in (
            f"/api/spike/runs/{target}/archive",
            f"/api/spike/events/{event_id}",
            f"/api/spike/reports/{report_id}",
            f"/api/spike/reports/{report_id}/download",
        ):
            response = await client.get(url)
            assert response.status_code == 404, url
            assert response.json()["error"]["code"] == "not_found"


async def test_웹자료_저장소가_없는_서비스는_웹_단계를_오류없이_건너뛴다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):  # 기본 대역: web_store 없음
        assert not hasattr(app.state.service, "web_store")
        target = await finish(client, app, "웹 없음", "r1")
        response = await delete(client, target)
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == []
        assert response.json()["counts"]["web_sources"] == 0
        assert not (tmp_path / "웹자료").exists()


async def test_웹자료_DB가_없으면_삭제가_DB를_새로_만들지_않는다(tmp_path):
    async with deletion_client(tmp_path, WebDoubleService) as (client, app):
        target = await finish(client, app, "웹 DB 없음", "r1")
        assert (await delete(client, target)).status_code == 200
        assert not (tmp_path / "웹자료").exists()


# ---------------------------------------------------------------- 차단 규칙


async def test_진행중_실행은_409_run_running이고_아무것도_바꾸지_않는다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        run_id = await start(client, "진행 중", "r1")
        db_before = app.state.service.repo.path.read_bytes()
        response = await delete(client, run_id)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "run_running"
        assert app.state.service.repo.path.read_bytes() == db_before


async def test_다른_실행이_진행_중이어도_끝난_실행은_지울_수_있다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        await finish(client, app, "완료 기준", "r1")
        # 두 번째 실행을 취소해 보고서 없는 끝난 실행으로 만든다(후속 실행이 이어받지 않음).
        second = await start(client, "취소될 질문", "r2")
        cancel = await client.post(
            "/api/spike/cancel",
            json={"run_id": second, "control_revision": 1, "client_request_id": "c1"},
        )
        assert cancel.status_code == 200, cancel.text
        third = await start(client, "진행 중인 세 번째", "r3")
        before = await snapshot(client)
        assert run_of(before, third)["status"] == "running"
        assert before["current_run_id"] == third

        response = await delete(client, second)

        assert response.status_code == 200, response.text
        # 지운 실행이 현재 실행이 아니므로 current_run_id는 그대로다.
        assert response.json()["current_run_id"] == third
        after = await snapshot(client)
        assert run_of(after, second) is None
        assert run_of(after, third) == run_of(before, third)
        assert after["current_run_id"] == third
        assert after["queue"] == before["queue"]
        assert after["queue_paused"] == before["queue_paused"]
        assert after["queue_pause_reason"] == before["queue_pause_reason"]
        assert after["selected_source_mode"] == before["selected_source_mode"]


async def test_후속_실행이_보고서를_쓰면_409_has_dependents_이고_후속을_지우면_허용된다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        first = await finish(client, app, "부모 질문", "r1")
        second = await finish(client, app, "자식 질문", "r2")
        assert (
            run_of(await snapshot(client), second)["parent_report_id"]
            == (run_of(await snapshot(client), first)["report_id"])
        )
        db_before = app.state.service.repo.path.read_bytes()

        blocked = await delete(client, first, request_id="d-first")

        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "has_dependents"
        assert app.state.service.repo.path.read_bytes() == db_before
        assert (await delete(client, second, request_id="d-second")).status_code == 200
        # 후속 실행이 지워졌으므로 같은 요청 ID가 아닌 새 요청은 이제 성공한다.
        allowed = await delete(client, first, request_id="d-first-again")
        assert allowed.status_code == 200, allowed.text
        assert (await snapshot(client))["runs"] == []


async def test_확인_ID가_다르면_422_confirm_mismatch이고_없는_실행이_먼저_404다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        run_id = await finish(client, app, "확인 불일치", "r1")
        db_before = app.state.service.repo.path.read_bytes()
        mismatch = await delete(client, run_id, confirm="다른-ID")
        assert mismatch.status_code == 422
        assert mismatch.json()["error"]["code"] == "confirm_mismatch"
        assert app.state.service.repo.path.read_bytes() == db_before
        # 검사 순서: 404(없는 실행)가 422(불일치)보다 먼저다.
        unknown = await delete(client, "없는실행", confirm="다른-ID")
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "not_found"


async def test_진행중이면서_확인_ID가_다르면_422가_먼저다(tmp_path):
    async with deletion_client(tmp_path) as (client, _app):
        run_id = await start(client, "순서 검사", "r1")
        response = await delete(client, run_id, confirm="다른-ID")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "confirm_mismatch"


async def test_본문_검증은_빈_요청_ID를_거절한다(tmp_path):
    async with deletion_client(tmp_path) as (client, _app):
        response = await client.post(
            "/api/spike/runs/x/delete", json={"client_request_id": "", "confirm_run_id": "x"}
        )
        assert response.status_code == 422


async def test_없는_실행은_404다(tmp_path):
    async with deletion_client(tmp_path) as (client, _app):
        response = await delete(client, "없는실행")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------- 멱등


async def test_같은_요청_ID_재전송은_실행이_사라진_뒤에도_저장된_응답을_돌려준다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        run_id = await finish(client, app, "멱등 검사", "r1")
        first = await delete(client, run_id, request_id="once")
        assert first.status_code == 200
        seq = (await snapshot(client))["high_watermark_seq"]
        again = await delete(client, run_id, request_id="once")
        assert again.status_code == 200, again.text
        assert again.json() == first.json()
        # 재전송은 흔적 사건을 또 만들지 않는다.
        assert (await snapshot(client))["high_watermark_seq"] == seq
        # 같은 ID를 다른 실행에 쓰면 충돌이다.
        other = await client.post(
            "/api/spike/runs/다른/delete", json=delete_body("다른", request_id="once")
        )
        assert other.status_code == 409
        assert other.json()["error"]["code"] == "request_id_conflict"


# ---------------------------------------------------------------- current_run_id


async def test_현재_실행을_지우면_남은_실행_중_가장_최근으로_바뀌고_없으면_null이다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        first = await finish(client, app, "첫째", "r1")
        second = await finish(client, app, "둘째", "r2")
        assert (await snapshot(client))["current_run_id"] == second
        response = await delete(client, second, request_id="d2")
        assert response.status_code == 200
        assert response.json()["current_run_id"] == first
        assert (await snapshot(client))["current_run_id"] == first
        last = await delete(client, first, request_id="d1")
        assert last.json()["current_run_id"] is None
        state = await snapshot(client)
        assert state["current_run_id"] is None and state["runs"] == []


# ---------------------------------------------------------------- 흔적 사건


async def test_흔적_사건은_질문을_담지_않고_high_watermark_seq가_반드시_증가한다(tmp_path):
    question = "비밀스러운 고유 질문 문장 ZXQ-7731"
    async with deletion_client(tmp_path) as (client, app):
        await finish(client, app, "남길 질문", "r1")
        target = await finish(client, app, question, "r2")
        before = (await snapshot(client))["high_watermark_seq"]
        # 삭제할 실행이 가장 큰 seq를 소유한다(MAX(seq)가 줄어들 수 있는 최악의 경우).
        assert (
            sql(
                app,
                "SELECT MAX(seq) FROM events WHERE json_extract(body,'$.run_id')=?",
                target,
            )[0][0]
            == before
        )

        assert (await delete(client, target)).status_code == 200

        after = await snapshot(client)
        assert after["high_watermark_seq"] > before
        tomb = [e for e in after["events"] if e["type"] == "run.deleted"]
        assert len(tomb) == 1
        assert tomb[0]["seq"] == after["high_watermark_seq"]
        assert tomb[0]["run_id"] == "deletions"
        assert tomb[0]["control_revision"] == 0
        assert tomb[0]["actor"] == "dispatcher"
        full = app.state.service.repo.get_event(tomb[0]["event_id"])
        assert full["input"] == {"run_id": target}
        assert full["output"]["run_id"] == target
        assert set(full["output"]) == {"run_id", "counts", "deleted_at"}
        assert full["status"] == "applied"
        assert question not in json.dumps(full, ensure_ascii=False)
        dump = " ".join(row[0] for row in sql(app, "SELECT body FROM events"))
        assert question not in dump
        assert [e["seq"] for e in app.state.service.repo.events_after(before)] == [
            after["high_watermark_seq"]
        ]


async def test_마지막_실행을_지워도_seq가_되돌아가지_않는다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        target = await finish(client, app, "유일한 실행", "r1")
        before = (await snapshot(client))["high_watermark_seq"]
        assert (await delete(client, target)).status_code == 200
        assert (await snapshot(client))["high_watermark_seq"] > before


# ---------------------------------------------------------------- 커밋 뒤 실패


async def test_보고서_파일_삭제가_실패하면_200과_경고이고_DB는_이미_깨끗하다(tmp_path, monkeypatch):
    async with deletion_client(tmp_path) as (client, app):
        await finish(client, app, "남길", "r1")
        target = await finish(client, app, "지울", "r2")
        report_id = run_of(await snapshot(client), target)["report_id"]
        md = tmp_path / app.state.service.report_bytes(report_id)[2]
        original = Path.unlink

        def failing(self, *args, **kwargs):
            if self.name == md.name:
                raise PermissionError("잠긴 파일")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing)

        response = await delete(client, target)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "deleted"
        assert len(body["warnings"]) == 1
        assert any("가" <= ch <= "힣" for ch in body["warnings"][0])
        assert md.exists()
        assert run_of(await snapshot(client), target) is None
        assert sql(app, "SELECT COUNT(*) FROM reports WHERE report_id=?", report_id)[0][0] == 0


async def test_변환_파일_삭제가_실패하면_경고로_돌려주고_나머지는_계속_지운다(
    tmp_path, monkeypatch
):
    async with deletion_client(tmp_path) as (client, app):
        await finish(client, app, "남길", "r1")
        target = await finish(client, app, "지울", "r2")
        report_id = run_of(await snapshot(client), target)["report_id"]
        files = seed_exports(app, report_id)
        stuck = next(p for p in files if p.suffix == ".pdf")
        original = Path.unlink

        def failing(self, *args, **kwargs):
            if self == stuck:
                raise PermissionError("잠긴 파일")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failing)

        response = await delete(client, target)

        assert response.status_code == 200, response.text
        assert len(response.json()["warnings"]) == 1
        assert stuck.exists()
        assert [p for p in files if p != stuck and p.exists()] == []


async def test_보고서_파일명이_저장소_밖을_가리키면_지우지_않고_경고한다(tmp_path):
    outside = tmp_path / "밖" / "건드리면안됨.md"
    outside.parent.mkdir()
    outside.write_text("외부 파일", encoding="utf-8")
    data = tmp_path / "데이터"
    async with deletion_client(data) as (client, app):
        target = await finish(client, app, "경로 이탈", "r1")
        report_id = run_of(await snapshot(client), target)["report_id"]
        with sqlite3.connect(app.state.service.repo.path) as db:
            db.execute(
                "UPDATE reports SET filename=? WHERE report_id=?",
                ("../밖/건드리면안됨.md", report_id),
            )
        response = await delete(client, target)
        assert response.status_code == 200, response.text
        assert len(response.json()["warnings"]) == 1
        assert outside.read_text(encoding="utf-8") == "외부 파일"


# ---------------------------------------------------------------- 도우미 단위


async def test_purge는_해당_보고서의_항목과_본문만_지우고_폴더가_없으면_0이다(tmp_path):
    async with deletion_client(tmp_path) as (client, app):
        first = await finish(client, app, "A", "r1")
        second = await finish(client, app, "B", "r2")
        state = await snapshot(client)
        exports = app.state.exports
        assert exports.purge({run_of(state, first)["report_id"]}) == {"deleted": 0, "errors": []}
        assert not exports.root.exists()
        mine = seed_exports(app, run_of(state, first)["report_id"])
        theirs = seed_exports(app, run_of(state, second)["report_id"])
        (exports.root / "깨진.json").write_text("{not json", encoding="utf-8")
        (exports.root / "배열.json").write_text("[1]", encoding="utf-8")
        assert exports.count_files({run_of(state, first)["report_id"]}) == 4
        result = exports.purge({run_of(state, first)["report_id"]})
        assert result == {"deleted": 4, "errors": []}
        assert not any(p.exists() for p in mine) and all(p.exists() for p in theirs)
        assert (exports.root / "깨진.json").exists() and (exports.root / "배열.json").exists()


async def test_WebSourceStore_count_run과_delete_run(tmp_path):
    store = seed_web(tmp_path, "run-a", "run-b")
    target_sources = [s["source_id"] for s in store.sources("run-a", 1)]
    assert len(target_sources) == 2
    assert web_counts(store.path, "run-a", target_sources)["web_history"] == 2
    assert store.count_run("run-a") == {"web_sources": 2, "web_reads": 1}
    assert store.count_run("없음") == {"web_sources": 0, "web_reads": 0}
    assert store.delete_run("run-a") == {"web_sources": 2, "web_reads": 1}
    assert store.count_run("run-a") == {"web_sources": 0, "web_reads": 0}
    assert store.count_run("run-b") == {"web_sources": 1, "web_reads": 1}
    assert web_counts(store.path, "run-b")["web_history"] == 1
    assert web_counts(store.path, "run-a", target_sources)["web_history"] == 0
