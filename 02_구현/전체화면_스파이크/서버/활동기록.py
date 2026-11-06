"""관측 경과 구간. node는 모델 대기를 포함하며 CPU 시간/합산 비용이 아니다."""

import asyncio
import json
from contextvars import ContextVar
from functools import wraps
from time import perf_counter

from 서버.저장소 import encode, now, uid

# task별 독립 목록. Send 병렬 작업은 노드 진입에서 새 context를 설정한다.
_current = ContextVar("activity_spans", default=())


class Activity:
    def __init__(self, run_id, revision, actor, track, label, kind, call_id=None):
        self.started = perf_counter()
        self.call_id = call_id
        self.statuses = []
        self.body = {
            "span_id": uid(),
            "run_id": run_id,
            "control_revision": revision,
            "actor": actor,
            "track_id": track,
            "label": label,
            "kind": kind,
            "started_at": now(),
            "ended_at": None,
            "duration_ms": None,
            "status": "running",
            "timing_source": "monotonic",
            "event_ids": [],
        }

    def save(self, db):
        db.execute(
            "INSERT INTO activity_spans(span_id,run_id,body) VALUES(?,?,?) "
            "ON CONFLICT(span_id) DO UPDATE SET body=excluded.body",
            (self.body["span_id"], self.body["run_id"], encode(self.body)),
        )

    def finish(self, db, status):
        if self.body["ended_at"] is None:
            self.body.update(
                ended_at=now(), duration_ms=max(0.0, (perf_counter() - self.started) * 1000)
            )
        self.body["status"] = status
        self.save(db)


def attach_event(db, event):
    spans = _current.get()
    call_id = next(
        (
            value["call_id"]
            for key in ("output", "input")
            if isinstance(value := event.get(key), dict) and value.get("call_id")
        ),
        None,
    )
    # 기존 대역은 종료 payload에 call_id가 없다. 같은 task/actor의 유일한
    # 미연결 호출만 허용하며 payload 모양은 바꾸지 않는다. 실제 제공자는 이 추론 금지.
    compatibility = [
        s
        for s in spans
        if s.call_id
        and s.body["actor"] == event["actor"]
        and not getattr(s, "terminal_linked", False)
    ]
    for span in spans:
        if span.call_id:
            if event["provider"] == "test-double" and call_id is None:
                if event["type"] not in {
                    "model.completed",
                    "model.failed",
                    "draft.created",
                    "result.superseded",
                }:
                    continue
                if len(compatibility) != 1 or compatibility[0] is not span:
                    continue
            elif call_id != span.call_id or event["type"] not in {
                "model.started",
                "model.completed",
                "model.failed",
                "result.superseded",
            }:
                continue
            if event["type"] != "model.started":
                span.terminal_linked = True
                if event["provider"] == "test-double" and event["status"] == "superseded":
                    span.body["status"] = "superseded"
        elif span.body["ended_at"] is not None:
            continue
        if (span.body["run_id"], span.body["control_revision"]) == (
            event["run_id"],
            event["control_revision"],
        ):
            span.body["event_ids"].append(event["event_id"])
            span.statuses.append(event["status"])
            span.save(db)


def start_call(db, state, actor, call_id, *, provider="test-double"):
    spans = _current.get()
    if not isinstance(spans, list):
        return
    span = Activity(
        state["run_id"], state["revision"], actor, actor, "model", "model_wait", call_id
    )
    if provider == "openai":
        span.body["call_id"] = call_id
    spans.append(span)
    span.save(db)


async def measured_call(repo, call_id, operation):
    span = next((s for s in _current.get() if s.call_id == call_id), None)
    status = "completed"
    try:
        return await operation
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except BaseException:
        status = "failed"
        raise
    finally:
        if span:
            with repo.connection() as db:
                span.finish(db, status)
                db.commit()


def outcome(span, run, revision, error_status=None):
    if error_status:
        return error_status
    if "superseded" in span.statuses:
        return "superseded"
    if "failed" in span.statuses or span.body["status"] == "failed":
        return "failed"
    if run and run["control_revision"] != revision:
        return "superseded"
    if run and run["status"] in {"cancelled", "interrupted"}:
        return run["status"]
    return "completed"


def measured_node(label, actor, track):
    def decorate(method):
        @wraps(method)
        async def wrapped(self, state):
            with self.repo.connection() as db:
                data = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
                active = self.repo.fenced(data, state["run_id"], state["revision"])
                if active:
                    who = state["assignment"]["assignee"] if label == "research" else actor
                    span = Activity(
                        state["run_id"],
                        state["revision"],
                        who,
                        who if track == "actor" else track,
                        label,
                        "node",
                    )
                    span.save(db)
                    db.commit()
            if not active:
                return await method(self, state)
            spans = [span]
            token = _current.set(spans)
            error_status = None
            try:
                return await method(self, state)
            except asyncio.CancelledError:
                error_status = "cancelled"
                raise
            except BaseException:
                error_status = "failed"
                raise
            finally:
                try:
                    with self.repo.connection() as db:
                        data = json.loads(
                            db.execute("SELECT body FROM state WHERE id=1").fetchone()[0]
                        )
                        run = self.repo.run(data, state["run_id"])
                        for item in spans:
                            # 요청 완료와 노드/파싱/결과 적용의 성패는 다른 사실이다.
                            if item.call_id and item.body["ended_at"] is not None:
                                continue
                            item.finish(db, outcome(item, run, state["revision"], error_status))
                        db.commit()
                finally:
                    _current.reset(token)

        return wrapped

    return decorate


def measured_publish(method):
    @wraps(method)
    def wrapped(self, db, data, run_id, revision):
        if not self.repo.fenced(data, run_id, revision):
            return method(self, db, data, run_id, revision)
        span = Activity(run_id, revision, "publisher", "document", "publish", "node")
        span.save(db)
        token = _current.set([*_current.get(), span])
        status = "completed"
        try:
            return method(self, db, data, run_id, revision)
        except BaseException:
            status = "failed"
            raise
        finally:
            span.finish(db, status)
            _current.reset(token)

    return wrapped
