"""주입 경계로 실제 6노드·저널·출처 receipt 검증. 네트워크/키/사용자 DB 없음."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from 서버.보고서근거 import build_draft_request
from 서버.실제연구제어 import RealResearchService
from 서버.웹계약 import WebSourceError

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "01_참고자료/원본데이터/corpus.json"


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    import socket

    import httpcore
    import httpx

    def denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    async def async_denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", async_denied)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class FakeWebStore:
    def __init__(self):
        self.rows = {}
        self.claims = {}

    def register_candidates(self, run_id, revision, hits):
        result = []
        for hit in hits:
            key = (run_id, revision, hit["url"])
            if key not in self.rows:
                self.rows[key] = {
                    "source_id": f"source-{len(self.rows) + 1}",
                    "run_id": run_id,
                    "control_revision": revision,
                    "url": hit["url"],
                    "final_url": None,
                    "title": hit["title"],
                    "snippet": hit["snippet"],
                    "status": "discovered",
                    "text": None,
                    "text_sha256": None,
                    "html_sha256": None,
                    "fetched_at": None,
                    "published_at": None,
                    "extracted_date_candidate": None,
                    "warnings": [],
                    "error_code": None,
                }
            result.append(copy.deepcopy(self.rows[key]))
        return result

    def get(self, run_id, revision, source_id):
        return copy.deepcopy(
            next(
                r
                for (run, rev, _), r in self.rows.items()
                if (run, rev, r["source_id"]) == (run_id, revision, source_id)
            )
        )

    def save_fetch(self, run_id, revision, source_id, page):
        row = self.get(run_id, revision, source_id)
        assert page["text_sha256"] == sha(page["text"])
        if row["status"] != "ready":
            row.update(
                {
                    k: page[k]
                    for k in (
                        "final_url",
                        "title",
                        "text",
                        "text_sha256",
                        "html_sha256",
                        "fetched_at",
                        "published_at",
                        "extracted_date_candidate",
                        "warnings",
                    )
                }
            )
            row["status"] = "ready"
            self.rows[(run_id, revision, row["url"])] = row
        return copy.deepcopy(row)

    def mark_failed(self, run_id, revision, source_id, code):
        row = self.get(run_id, revision, source_id)
        row.update(status="failed", error_code=code)
        self.rows[(run_id, revision, row["url"])] = row

    def mark_read(self, run_id, revision, source_id, section_id, model_call_id, read_text, note):
        row = self.get(run_id, revision, source_id)
        assert (
            row["status"] == "ready" and read_text == row["text"][:20000] and 0 < len(note) <= 6000
        )
        key = (run_id, revision, source_id, section_id)
        self.claims[key] = {
            "source_id": source_id,
            "section_id": section_id,
            "model_call_id": model_call_id,
            "read_text_sha256": sha(read_text),
            "note_sha256": sha(note),
        }

    def sources(self, run_id, revision):
        return copy.deepcopy(
            [r for (run, rev, _), r in self.rows.items() if (run, rev) == (run_id, revision)]
        )

    def read_claims(self, run_id, revision):
        return copy.deepcopy(
            [r for (run, rev, _, _), r in self.claims.items() if (run, rev) == (run_id, revision)]
        )


class FakeWebClient:
    def __init__(self, *, empty=False, fail=False, block=False, threaded=False):
        self.empty, self.fail, self.block, self.threaded = empty, fail, block, threaded
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def emit(self, observer, operation, url):
        payload = {
            "operation": operation,
            "web_request_id": f"http-{len(self.calls)}",
            "method": "GET",
            "url": url,
            "started_at": "2026-11-07T00:00:00+00:00",
        }
        if self.threaded:
            await asyncio.to_thread(observer, "request.started", payload)
        else:
            observer("request.started", payload)
        self.started.set()
        if self.block:
            await self.release.wait()
        observer("request.completed", {**payload, "status_code": 200, "elapsed_ms": 1})

    async def search(self, query, *, observer=None):
        self.calls.append(("search", query))
        await self.emit(observer, "search", "https://search.example/search")
        return {
            "query": query,
            "backend": "explicit-fake",
            "results": []
            if self.empty
            else [
                {
                    "title": "동일 제목",
                    "url": f"https://public.example/{i}",
                    "snippet": "검색 설명은 읽기 근거 아님",
                }
                for i in range(1, 5)
            ],
            "elapsed_ms": 1,
            "observed_requests": 1,
        }

    async def fetch(self, url, *, observer=None):
        self.calls.append(("fetch", url))
        await self.emit(observer, "fetch", url)
        if self.fail:
            raise WebSourceError("fetch_blocked")
        text = "공개 자료 사실 및 범위. " * 3000
        return {
            "requested_url": url,
            "final_url": url,
            "title": "동일 제목",
            "text": text,
            "text_sha256": sha(text),
            "html_sha256": sha("html"),
            "fetched_at": "2026-11-07T00:00:00+00:00",
            "published_at": None,
            "extracted_date_candidate": None,
            "warnings": [],
            "elapsed_ms": 1,
            "observed_requests": 1,
        }

    async def aclose(self):
        self.closed = True


class WebModel:
    def __init__(self, *, long_note=False, bad_first_read=None):
        self.calls = []
        self.schemas = []
        self.notes = {}
        self.excerpts = {}
        self.long_note = long_note
        # "long" | "empty": 첫 읽기 응답만 메모 길이 계약을 어긴다(나머지는 정상).
        self.bad_first_read = bad_first_read
        self.reads = 0

    async def complete(self, system, user, *, context, on_attempt, response_schema=None, **kwargs):
        self.calls.append((system, user, context))
        self.schemas.append(response_schema)
        attempt = f"attempt-{len(self.calls)}"
        on_attempt(attempt)
        op = context["operation"]
        actor = context["actor"]
        if op == "plan":
            value = {
                "제목": "웹 연구",
                "목차": [
                    {
                        "절": "자료 범위",
                        "지시": "범위를 확인",
                        "역할": "큰그림 담당",
                        "검색어": ["공개 자료"],
                    }
                ],
            }
        elif op == "refine_queries":
            value = {"검색어": ["추가 공개 자료"]}
        elif op == "select":
            # 후보 목록은 JSON source snapshot 카드로 전달된다.
            cards = json.loads(user)
            value = {"문서": cards["candidates"][0]["source_id"] if cards["candidates"] else None}
        elif op == "read_one":
            sid = user.split("[source_id: ", 1)[1].split("]", 1)[0]
            self.excerpts[sid] = user.split("]\n", 1)[1][:80]
            self.reads += 1
            if self.long_note or (self.bad_first_read == "long" and self.reads == 1):
                value = "메모" * 3001
            elif self.bad_first_read == "empty" and self.reads == 1:
                value = ""
            else:
                value = "메모" * 8
            self.notes[actor] = value
        elif op == "draft_section":
            catalog = json.loads(user)
            assert set(catalog) == {"assignment", "notes", "evidence_set_id", "evidence"}
            offered = catalog["evidence"]
            assert all(type(e["id"]) is int for e in offered)
            assert response_schema is not None, "normal web draft must forward response_schema"
            assert response_schema["properties"]["근거목록ID"]["enum"] == [
                catalog["evidence_set_id"]
            ]
            assert response_schema["properties"]["문장"]["items"]["properties"]["근거구간"][
                "items"
            ]["enum"] == [e["id"] for e in offered]
            assert offered and all(e["end"] <= 20000 for e in offered)
            assert {e["source_id"] for e in offered} <= self.excerpts.keys()
            value = {
                "근거목록ID": catalog["evidence_set_id"],
                "문장": [
                    {
                        "내용": "자료에 적힌 범위를 벗어나지 않는 대역 문장입니다. " * 88,
                        "근거구간": [offered[0]["id"]],
                    }
                ],
                "충분": True,
                "부족": "",
            }
        elif op == "synthesize":
            value = {
                "머리말": "공개 자료를 정리합니다.",
                "맺음말": "자료 범위를 확인해야 합니다.",
                "표": [],
                "도식": [],
                "시각화생략사유": "대역에서는 시각화를 생략한다.",
            }
        elif op == "semantic_review":
            value = {"통과": True, "문제": [], "검토요약": "명시적 대역 판정; 실제 의미 검증 아님"}
        else:
            raise AssertionError(op)
        return {
            "content": json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value,
            "attempt_id": attempt,
            "request_id": "provider-not-app-call",
            "model": "explicit-fake",
            "usage": {},
        }

    async def aclose(self):
        pass


async def settle(service):
    for _ in range(2000):
        if not service.tasks:
            return
        await asyncio.sleep(0.002)
    raise AssertionError("not settled")


def make(*, client=None, model=None, store=None):
    return RealResearchService(
        ROOT / ".cache/보고서품질_검증/번호참조/통합/웹" / uuid4().hex,
        model or WebModel(),
        CORPUS,
        web_client=client or FakeWebClient(),
        web_store=store or FakeWebStore(),
    )


@pytest.mark.parametrize("redirected", [False, True])
async def test_웹6노드_진짜읽기receipt_출처정본_모델과HTTP분리(redirected):
    class RedirectClient(FakeWebClient):
        async def fetch(self, url, *, observer=None):
            page = await super().fetch(url, observer=observer)
            if redirected:
                page["final_url"] += "/final"
            return page

    service = make(client=RedirectClient(threaded=True))
    try:
        a = service.admit("/웹 공개 자료 범위", "web")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "completed", run
        claims = service.web_store.read_claims(a["run_id"], 1)
        assert len(claims) == 3
        with service.repo.read_connection() as db:
            for claim in claims:
                row = db.execute(
                    "SELECT status,input FROM calls WHERE call_id=?", (claim["model_call_id"],)
                ).fetchone()
                assert row["status"] == "completed"
                assert json.loads(row["input"])["context"]["operation"] == "read_one"
        report = service.report_bytes(run["report_id"])[0]
        assert report["source_mode"] == "web"
        assert len(report.get("sources", [])) == 3
        if redirected:
            assert all(
                row["final_url"] != row["url"] for row in service.web_store.sources(a["run_id"], 1)
            )
            assert all("/final" in row["final_url"] for row in report["sources"])
        assert all(
            r["read_text_sha256"]
            == sha(service.web_store.get(a["run_id"], 1, r["source_id"])["text"][:20000])
            for r in report["sources"]
        )
        assert "https://public.example/" in report["markdown"]
        assert "wikipedia.org" not in report["markdown"] and "CC BY-SA" not in report["markdown"]
        assert run["web_usage"] == {
            "search_attempts": 1,
            "search_limit": 16,
            "fetch_attempts": 3,
            "fetch_limit": 24,
            "observed_http_requests": 4,
        }
        assert run["actual_model_requests"] == 10  # 기획1 + 선택3 + 읽기3 + 절1 + 종합1 + 의미검토1
        events = service.repo.archive(a["run_id"])["events"]
        assert any(e["type"] == "web.request.started" for e in events)
        assert any(
            e["type"] == "tool.completed" and e["output"].get("read_text_sha256") for e in events
        )
        assert all(e["source_mode"] == "web" for e in events if e["run_id"] == a["run_id"])
    finally:
        await service.close()


async def test_웹초안은_검증된_source_id의_제시구간만_코드로_인용한다():
    """메모 제목을 인용하던 반례를 실제 제시 구간→코드 source_id 경계로 옮긴다."""
    service = make()
    try:
        a = service.admit("/웹 자료", "web")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        _system, user, _ = next(
            c for c in service.model.calls if c[2]["operation"] == "draft_section"
        )
        assert user.startswith("{"), "web draft_section must receive the source-span JSON catalog"
        catalog = json.loads(user)
        assert run["status"] == "completed", run
        ids = [c["source_id"] for c in service.web_store.read_claims(a["run_id"], 1)]
        assert {e["source_id"] for e in catalog["evidence"]} == set(ids)
        for e in catalog["evidence"]:
            source = service.web_store.get(a["run_id"], 1, e["source_id"])
            assert e["text"] == source["text"][e["start"] : e["end"]]
            assert e["end"] <= 20000
        section = next(
            e["output"]
            for e in service.repo.archive(a["run_id"])["events"]
            if e["type"] == "research.completed"
        )
        assert section["근거"][0]["출처"] in ids
        assert "메모 제목" not in section["본문"]
        assert run["quality"]["passed"] and run["quality"]["unknown_citations"] == []
    finally:
        await service.close()


@pytest.mark.parametrize("client", [FakeWebClient(empty=True), FakeWebClient(fail=True)])
async def test_읽기없는웹은_코드기원부족_2라운드후_품질차단(client):
    service = make(client=client)
    try:
        a = service.admit("/웹 자료", "web")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "blocked", run
        assert run["research_stop_reason"] == "round_exhausted"
        assert "web_sources_insufficient" in run["quality"]["reasons"]
        assert not service.web_store.read_claims(a["run_id"], 1)
        assert service.repo.snapshot()["queue_paused"]
        assert not any(
            c[2]["operation"] in ("read_one", "draft_section") for c in service.model.calls
        )
        fetched = [value for operation, value in client.calls if operation == "fetch"]
        assert len(fetched) == len(set(fetched)), "실패한 출처를 다음 라운드에서 재시도하지 않는다"
        failures = [
            e for e in service.repo.archive(a["run_id"])["events"] if e["type"] == "tool.failed"
        ]
        assert all(e["input"].get("url") in fetched for e in failures)
    finally:
        await service.close()


async def test_늦은검색은_출처쓰기_후속fetch_읽기_정본없음():
    client = FakeWebClient(block=True)
    service = make(client=client)
    try:
        a = service.admit("/웹 자료", "old")
        await asyncio.wait_for(client.started.wait(), 3)
        service.cancel(a["run_id"], 1, "cancel")
        client.release.set()
        await settle(service)
        assert client.calls == [("search", "공개 자료")]
        assert not service.web_store.sources(a["run_id"], 1)
        assert len(service.model.calls) == 1
        assert service.repo.snapshot()["runs"][0]["status"] == "cancelled"
    finally:
        await service.close()


async def test_malformed_redirect_candidate_does_not_abort_other_sources():
    from 서버.웹자료 import FreeWebClient
    from 테스트.테스트_웹자료 import WireBackend, public_dns, response

    wire = WireBackend(
        response(status=302, headers={"Location": "http://["}), response(), response(), response()
    )
    real_fetch = FreeWebClient(resolver=public_dns, network_backend=wire)

    class FetchBoundary(FakeWebClient):
        async def fetch(self, url, *, observer=None):
            self.calls.append(("fetch", url))
            return await real_fetch.fetch(url, observer=observer)

    service = make(client=FetchBoundary())
    try:
        admitted = service.admit("/웹 공개 자료", "redirect")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "completed", run
        sources = service.web_store.sources(admitted["run_id"], 1)
        assert sources[0]["status"] == "failed" and sources[0]["error_code"] == "invalid_url"
        # 연구 루프는 성공 수가 아닌 선택 시도 3회다: 실패 1 + 정상 읽기 2.
        assert len(service.web_store.read_claims(admitted["run_id"], 1)) == 2
        assert len(wire.connects) == 3
        report = service.report_bytes(run["report_id"])[0]
        assert len(report["sources"]) == 2
        assert all(row["source_id"] != sources[0]["source_id"] for row in report["sources"])
    finally:
        await service.close()
        await real_fetch.aclose()


@pytest.mark.parametrize(
    "kind,code", [("long", "read_note_too_long"), ("empty", "read_note_empty")]
)
async def test_길이계약을_어긴_메모는_읽기로_인정하지_않고_연구는_계속한다(kind, code):
    """2026-11-07 실사용: 메모 6,156자(상한 6,000)가 연구 전체를 failed로 끝냈다.

    읽기 claim은 그대로 없다(정본 불변). 다만 모델이 길이를 못 맞춘 출처 하나 때문에 다른
    출처를 읽은 결과까지 버리지 않고, 거절 사유를 저널에 남긴 채 다음 출처로 계속한다.
    """
    service = make(model=WebModel(bad_first_read=kind))
    try:
        a = service.admit("/웹 자료", "web")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "completed", run
        claims = service.web_store.read_claims(a["run_id"], 1)
        assert len(claims) == 2  # 선택 3회 중 첫 읽기는 거절되고 나머지 둘만 인정된다.
        events = service.repo.archive(a["run_id"])["events"]
        rejected = [
            e
            for e in events
            if e["type"] == "tool.failed" and (e.get("error") or {}).get("code") == code
        ]
        assert len(rejected) == 1
        rejected_source = rejected[0]["input"]["source_id"]
        assert rejected_source not in {c["source_id"] for c in claims}
        report = service.report_bytes(run["report_id"])[0]
        assert len(report["sources"]) == 2
        assert all(row["source_id"] != rejected_source for row in report["sources"])
        assert (
            run["actual_model_requests"] == 10
        )  # 거절된 읽기도 실제 모델 호출이며 의미검토1 추가.
    finally:
        await service.close()


async def test_모든_메모가_길면_읽기없이_2라운드후_품질차단되고_실패하지_않는다():
    service = make(model=WebModel(long_note=True))
    try:
        a = service.admit("/웹 자료", "web")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "blocked", run
        assert run["research_stop_reason"] == "round_exhausted"
        assert "web_sources_insufficient" in run["quality"]["reasons"]
        assert not service.web_store.read_claims(a["run_id"], 1)
        assert any(c[2]["operation"] == "read_one" for c in service.model.calls)
        assert service.repo.snapshot()["queue_paused"]
    finally:
        await service.close()


async def test_새버전은_웹논리상한_다시0_옛버전사용량불변():
    service = make()
    service.spawn = lambda *args: None
    try:
        first = service.admit("/웹 질문", "first")
        with service.repo.transaction() as (db, data):
            run = service.repo.run(data, first["run_id"])
            run["web_usage"] = {
                "search_attempts": 16,
                "fetch_attempts": 24,
                "observed_http_requests": 90,
            }
            service.repo.save_revision(db, run)
        revised = service.admit("보완 질문", "revised")
        run = service.repo.snapshot()["runs"][0]
        assert run.get("web_usage") == {
            "search_attempts": 0,
            "search_limit": 16,
            "fetch_attempts": 0,
            "fetch_limit": 24,
            "observed_http_requests": 0,
        }
        archive = service.repo.archive(first["run_id"])
        assert archive["revisions"][0]["web_usage"]["search_attempts"] == 16
        assert revised["control_revision"] == 2
    finally:
        await service.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "actual_user",
        "source_url",
        "read_url",
        "fetch_url",
        "wrong_section",
        "wrong_run",
        "wrong_revision",
        "failed_call",
        "wrong_read_hash",
        "wrong_note_hash",
        "failed_source",
        "no_fetch_receipt",
        "orphan_call",
    ],
)
async def test_부정확한읽기receipt는_출처claim만으로_제출불가(tamper):
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedEditor(WebModel):
        async def complete(self, system, user, *, context, **kwargs):
            if context["operation"] == "synthesize":
                entered.set()
                await release.wait()
            return await super().complete(system, user, context=context, **kwargs)

    service = make(model=DelayedEditor())
    try:
        service.admit("/웹 자료", "first")
        await asyncio.wait_for(entered.wait(), 3)
        assert len(service.web_store.claims) == 3  # 실제 읽기가 완료된 양성 대조 뒤 변조.
        with service.repo.transaction() as (db, _data):
            for row in db.execute("SELECT call_id,input FROM calls").fetchall():
                payload = json.loads(row["input"])
                if payload["context"]["operation"] == "read_one":
                    if tamper == "read_url":
                        payload["inputs"]["url"] = "https://other.example/"
                        db.execute(
                            "UPDATE calls SET input=? WHERE call_id=?",
                            (json.dumps(payload), row["call_id"]),
                        )
                    if tamper == "actual_user":
                        payload["user"] = "읽지 않은 다른 본문"
                        db.execute(
                            "UPDATE calls SET input=? WHERE call_id=?",
                            (json.dumps(payload), row["call_id"]),
                        )
                    elif tamper == "wrong_run":
                        db.execute(
                            "UPDATE calls SET run_id='different-run' WHERE call_id=?",
                            (row["call_id"],),
                        )
                    elif tamper == "wrong_revision":
                        db.execute(
                            "UPDATE calls SET revision=999 WHERE call_id=?", (row["call_id"],)
                        )
                    elif tamper == "failed_call":
                        db.execute(
                            "UPDATE calls SET status='failed' WHERE call_id=?", (row["call_id"],)
                        )
            if tamper == "fetch_url":
                db.execute(
                    "UPDATE events SET body=json_set(body, '$.output.url', 'https://other.example/') WHERE json_extract(body,'$.type')='web.source.fetched'"
                )
            if tamper == "no_fetch_receipt":
                db.execute(
                    "DELETE FROM events WHERE json_extract(body,'$.type')='web.source.fetched'"
                )
        for claim in service.web_store.claims.values():
            if tamper == "wrong_section":
                claim["section_id"] = "section-not-assigned"
            elif tamper == "wrong_read_hash":
                claim["read_text_sha256"] = "0" * 64
            elif tamper == "wrong_note_hash":
                claim["note_sha256"] = "0" * 64
            elif tamper == "orphan_call":
                claim["model_call_id"] = "provider-not-app-call"
        if tamper == "source_url":
            for source in service.web_store.rows.values():
                source["final_url"] = "https://other.example/"
        if tamper == "failed_source":
            for source in service.web_store.rows.values():
                source["status"] = "failed"
        release.set()
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "blocked"
        assert "citation_integrity_error" in run["quality"]["reasons"]
        assert "web_sources_insufficient" in run["quality"]["reasons"]
        assert run["report_id"] is None
    finally:
        release.set()
        await service.close()


async def test_single_sidecar_url_change_cannot_publish_cited_source():
    import sqlite3

    from 서버.웹자료저장소 import WebSourceStore

    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedEditor(WebModel):
        async def complete(self, system, user, *, context, **kwargs):
            if context["operation"] == "synthesize":
                entered.set()
                await release.wait()
            return await super().complete(system, user, context=context, **kwargs)

    store = WebSourceStore(
        ROOT / ".cache/보고서품질_검증/번호참조/통합/웹" / uuid4().hex / "웹자료/자료.sqlite3"
    )
    service = make(model=DelayedEditor(), store=store)
    try:
        admitted = service.admit("/웹 공개 자료", "single-url")
        await asyncio.wait_for(entered.wait(), 3)
        claims = service.web_store.read_claims(admitted["run_id"], 1)
        assert len(claims) == 3
        section = next(
            e["output"]
            for e in service.repo.archive(admitted["run_id"])["events"]
            if e["type"] == "research.completed"
        )
        sid = section["근거"][0]["출처"]  # 읽기 순서 대신 원고가 실제 인용한 자료를 변조한다.
        assert sid in {claim["source_id"] for claim in claims}
        before = service.web_store.get(admitted["run_id"], 1, sid)
        with sqlite3.connect(service.web_store.path) as db:
            db.execute(
                "UPDATE web_sources SET body=json_set(body, '$.final_url', ?) WHERE source_id=?",
                ("https://unread.example/not-fetched", sid),
            )
        after = service.web_store.get(admitted["run_id"], 1, sid)
        assert {k: v for k, v in before.items() if k != "final_url"} == {
            k: v for k, v in after.items() if k != "final_url"
        }
        assert service.web_store.read_claims(admitted["run_id"], 1) == claims
        release.set()
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "blocked"
        assert "citation_integrity_error" in run["quality"]["reasons"]
        assert run["report_id"] is None
        assert all(row["source_id"] != sid for row in run["source_provenance"])
    finally:
        release.set()
        await service.close()


async def test_웹논리슬롯_동시예약은_상한이상_외부요청0():
    from 서버.웹연구 import request

    service = make()
    service.spawn = lambda *args: None
    try:
        a = service.admit("/웹 자료", "first")
        state = {"run_id": a["run_id"], "revision": 1, "source_mode": "web"}
        with service.repo.transaction() as (_db, data):
            run = service.repo.run(data, a["run_id"])
            run["web_usage"] = {
                "search_attempts": 15,
                "fetch_attempts": 23,
                "observed_http_requests": 0,
            }
        search = await asyncio.gather(
            request(service, state, "search", "양성"), request(service, state, "search", "차단")
        )
        fetch = await asyncio.gather(
            request(service, state, "fetch", "https://public.example/1"),
            request(service, state, "fetch", "https://public.example/2"),
        )
        assert search[0][0] is not None and search[1] == (None, "web_limit_reached")
        assert fetch[0][0] is not None and fetch[1] == (None, "web_limit_reached")
        assert service.web_client.calls == [
            ("search", "양성"),
            ("fetch", "https://public.example/1"),
        ]
        assert service.repo.snapshot()["runs"][0]["web_usage"] == {
            "search_attempts": 16,
            "fetch_attempts": 24,
            "observed_http_requests": 2,
        }
        assert service.repo.snapshot()["runs"][0]["actual_model_requests"] == 0
    finally:
        await service.close()


@pytest.mark.parametrize("stage", ["fetch", "read_one"])
async def test_취소후_늦은본문과읽기응답은_추가claim과후속모델0(stage):
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedClient(FakeWebClient):
        async def fetch(self, url, *, observer=None):
            response = await super().fetch(url, observer=observer)
            if stage == "fetch":
                entered.set()
                await release.wait()
            return response

    class DelayedRead(WebModel):
        async def complete(self, system, user, *, context, **kwargs):
            response = await super().complete(system, user, context=context, **kwargs)
            if context["operation"] == stage:
                entered.set()
                await release.wait()
            return response

    service = make(client=DelayedClient(), model=DelayedRead())
    try:
        a = service.admit("/웹 자료", "first")
        await asyncio.wait_for(entered.wait(), 3)
        count = len(service.model.calls)
        requests = list(service.web_client.calls)
        service.cancel(a["run_id"], 1, "cancel")
        release.set()
        await settle(service)
        assert not service.web_store.read_claims(a["run_id"], 1)
        assert len(service.model.calls) == count
        assert service.web_client.calls == requests
        assert service.repo.snapshot()["runs"][0]["status"] == "cancelled"
        if stage == "fetch":
            assert all(r["status"] != "ready" for r in service.web_store.sources(a["run_id"], 1))
    finally:
        release.set()
        await service.close()


async def test_웹4절2라운드_16검색24본문_63모델상한_실제그래프대역():
    from urllib.parse import quote

    class DistinctSources(FakeWebClient):
        async def search(self, query, *, observer=None):
            result = await super().search(query, observer=observer)
            for item in result["results"]:
                item["url"] += "/" + quote(query)
            return result

    class FullModel(WebModel):
        def __init__(self):
            super().__init__()
            self.rounds = {}

        async def complete(self, system, user, *, context, **kwargs):
            response = await super().complete(system, user, context=context, **kwargs)
            operation, actor = context["operation"], context["actor"]
            if operation == "plan":
                value = {
                    "제목": "웹 전체 범위",
                    "목차": [
                        {
                            "절": f"내용 {i}",
                            "지시": "원자료 범위를 확인",
                            "역할": "큰그림 담당",
                            "검색어": [f"첫 {i} 가", f"첫 {i} 나"],
                        }
                        for i in range(4)
                    ],
                }
            elif operation == "refine_queries":
                value = {"검색어": [f"추가 {actor} 가", f"추가 {actor} 나"]}
            elif operation == "draft_section":
                self.rounds[actor] = self.rounds.get(actor, 0) + 1
                value = json.loads(response["content"])
                value.update(
                    충분=self.rounds[actor] == 2,
                    부족="추가 원자료 필요" if self.rounds[actor] == 1 else "",
                )
            else:
                return response
            return {**response, "content": json.dumps(value, ensure_ascii=False)}

    service = make(client=DistinctSources(), model=FullModel())
    try:
        a = service.admit("/웹 모든 범위", "max")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "completed", run
        assert run["actual_model_requests"] == len(service.model.calls) == 63
        assert run["web_usage"] == {
            "search_attempts": 16,
            "search_limit": 16,
            "fetch_attempts": 24,
            "fetch_limit": 24,
            "observed_http_requests": 40,
        }
        assert len(service.web_store.read_claims(a["run_id"], 1)) == 24
        assert service.model.rounds == {f"researcher-{i}": 2 for i in range(1, 5)}
        for actor in service.model.rounds:
            drafts = [
                c
                for c in service.model.calls
                if c[2]["operation"] == "draft_section" and c[2]["actor"] == actor
            ]
            catalogs = [json.loads(c[1]) for c in drafts]
            assert [len(c["notes"]) for c in catalogs] == [3, 6]
            assert catalogs[1]["notes"][:3] == catalogs[0]["notes"]
            for call, catalog in zip(drafts, catalogs):
                assert {e["source_id"] for e in catalog["evidence"]} == {
                    n[0] for n in catalog["notes"]
                }
                assert all(e["end"] <= 20000 for e in catalog["evidence"])
                assert len(catalog["evidence"]) <= 128
                schema = service.model.schemas[service.model.calls.index(call)]
                assert schema is not None
                assert (
                    len(call[0].encode("utf-8"))
                    + len(call[1].encode("utf-8"))
                    + len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
                    + 8192
                    <= 272000
                )
        archive = service.repo.archive(a["run_id"])
        assert all(e["source_mode"] == "web" for e in archive["events"])
        assert run["research_stop_reason"] == "sufficient"
    finally:
        await service.close()


@pytest.mark.parametrize("mutation", ["missing", "unread", "outside_excerpt"])
async def test_웹절근거는_receipt와_실제읽힌앞20000자에_귀속한다(mutation):
    quote = "실제 읽은 앞부분 밖에만 존재하는 문장입니다."

    class TailClient(FakeWebClient):
        async def fetch(self, url, *, observer=None):
            page = await super().fetch(url, observer=observer)
            page["text"] = "읽힌 원문에 존재하는 자료입니다. " * 1500 + quote
            page["text_sha256"] = sha(page["text"])
            return page

    class InvalidEvidenceModel(WebModel):
        async def complete(self, *args, **kwargs):
            result = await super().complete(*args, **kwargs)
            if kwargs["context"]["operation"] == "draft_section":
                obj = json.loads(result["content"])
                if mutation == "missing":
                    obj["문장"][0].pop("근거구간")
                else:
                    catalog = json.loads(args[1])
                    sid = catalog["evidence"][0]["source_id"]
                    if mutation == "unread":
                        extra = build_draft_request(catalog["assignment"], [], {"source-4": quote})
                        bad_id = json.loads(extra.prompts[1])["evidence"][0]["id"]
                    else:
                        source = service.web_store.get(
                            kwargs["context"]["run_id"], kwargs["context"]["revision"], sid
                        )
                        extra = build_draft_request(
                            {**catalog["assignment"], "instruction": quote},
                            [],
                            {sid: source["text"]},
                        )
                        tail = next(
                            e
                            for e in json.loads(extra.prompts[1])["evidence"]
                            if quote in e["text"]
                        )
                        assert tail["end"] > 20000
                        bad_id = tail["id"]
                    extra_catalog = json.loads(extra.prompts[1])
                    assert extra_catalog["evidence_set_id"] != catalog["evidence_set_id"]
                    obj["근거목록ID"] = extra_catalog["evidence_set_id"]
                    obj["문장"][0]["근거구간"] = [bad_id]
                result["content"] = json.dumps(obj, ensure_ascii=False)
            return result

    service = make(client=TailClient(), model=InvalidEvidenceModel())
    try:
        admission = service.admit("/웹 자료", "first")
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "failed" and run["report_id"] is None
        claims = service.web_store.read_claims(admission["run_id"], 1)
        assert len(claims) == 3  # 읽기 receipt 자체는 변형하지 않는다.
        assert not any(c[2]["operation"] == "synthesize" for c in service.model.calls)
        for row in service.web_store.sources(admission["run_id"], 1):
            if row["status"] == "ready":
                assert quote not in row["text"][:20000] and quote in row["text"]
        errors = [
            e["error"]["message"]
            for e in service.repo.archive(admission["run_id"])["events"]
            if e["error"]
        ]
        reason = "span response: schema" if mutation == "missing" else "span response: evidence set"
        assert reason in errors
    finally:
        await service.close()


@pytest.mark.parametrize("tamper", ["note_receipt", "source_version", "fetch_receipt"])
async def test_웹구간요청후_현재receipt와원문을_재검사하고_늦은조립을_차단한다(tamper):
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedDraft(WebModel):
        async def complete(self, *args, **kwargs):
            response = await super().complete(*args, **kwargs)
            if kwargs["context"]["operation"] == "draft_section":
                assert json.loads(args[1])["evidence"]
                entered.set()
                await release.wait()
            return response

    service = make(model=DelayedDraft())
    try:
        admitted = service.admit("/웹 자료", "first")
        await asyncio.wait_for(entered.wait(), 3)
        assert len(service.web_store.read_claims(admitted["run_id"], 1)) == 3
        if tamper == "note_receipt":
            for claim in service.web_store.claims.values():
                claim["note_sha256"] = "0" * 64
        elif tamper == "source_version":
            for source in service.web_store.rows.values():
                if source["status"] == "ready":
                    source["text"] = "새 원문 버전. " + source["text"]
                    source["text_sha256"] = sha(source["text"])
        else:
            with service.repo.transaction() as (db, _data):
                db.execute(
                    "DELETE FROM events WHERE json_extract(body,'$.type')='web.source.fetched'"
                )
        count = len(service.model.calls)
        requests = list(service.web_client.calls)
        release.set()
        await settle(service)
        run = service.repo.snapshot()["runs"][0]
        assert run["status"] == "failed" and run["report_id"] is None
        assert run["failure"]["message"] == "span response: source version"
        assert len(service.model.calls) == count and service.web_client.calls == requests
        events = service.repo.archive(admitted["run_id"])["events"]
        assert not any(e["type"] == "research.completed" for e in events)
        assert any(
            e["type"] == "model.completed" and '"근거구간"' in e["output"]["content"]
            for e in events
        )
    finally:
        release.set()
        await service.close()
