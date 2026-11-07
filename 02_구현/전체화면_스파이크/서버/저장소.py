"""단일 로컬 작업자의 SQLite 저널. 연결은 모든 경로에서 명시적으로 닫는다."""

import copy
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid4().hex


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def run_deleted(db, run_id):
    """쓰기 경계의 영구 삭제 펜스. 판정과 후속 쓰기를 삭제 커밋과 직렬화한다.

    state 존재만 검사하면 접수 중인 새 실행/기존 fixture도 거절하므로 영속 흔적을 쓴다.
    독립 연결의 활동 finally도 먼저 쓰기 잠금을 잡아 check→UPSERT 경합을 막는다.
    커밋/롤백은 연결을 소유한 호출자가 담당한다.
    """
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    return (
        db.execute(
            "SELECT 1 FROM events WHERE json_extract(body,'$.run_id')='deletions' "
            "AND json_extract(body,'$.type')='run.deleted' "
            "AND json_extract(body,'$.input.run_id')=? LIMIT 1",
            (run_id,),
        ).fetchone()
        is not None
    )


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class Repository:
    def __init__(self, data_dir: Path, *, provider="test-double", mode="spike"):
        if (provider, mode) not in {("test-double", "spike"), ("openai", "research")}:
            raise ValueError("저장소 제공자와 실행 모드의 조합을 확인해 주세요.")
        self.provider = provider
        self.mode = mode
        self.source_modes = ["local"]
        self.root = data_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "실행기록.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    run_id TEXT, revision INTEGER, body TEXT NOT NULL,
                    PRIMARY KEY(run_id, revision));
                CREATE TABLE IF NOT EXISTS calls (
                    call_id TEXT PRIMARY KEY, run_id TEXT, revision INTEGER,
                    actor TEXT, status TEXT, input TEXT, output TEXT, gate TEXT);
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY, body TEXT, response TEXT);
                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY, body TEXT, filename TEXT);
                CREATE TABLE IF NOT EXISTS activity_spans (
                    span_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, body TEXT NOT NULL);
            """)
            initial = {
                "selected_source_mode": "local",
                "current_run_id": None,
                "runs": [],
                "queue": [],
                "queue_paused": False,
                "queue_pause_reason": None,
            }
            db.execute("INSERT OR IGNORE INTO state VALUES(1,?)", (encode(initial),))
            db.commit()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def read_connection(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            try:
                yield db, state
                db.execute("UPDATE state SET body=? WHERE id=1", (encode(state),))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def run(state, run_id):
        return next((r for r in state["runs"] if r["run_id"] == run_id), None)

    def fenced(self, state, run_id, revision):
        run = self.run(state, run_id)
        if (
            run is None
            or run["status"] != "running"
            or run["control_revision"] != revision
            or state["current_run_id"] != run_id
        ):
            return None
        return run

    def event(
        self,
        db,
        run_id,
        revision,
        kind,
        actor,
        *,
        input=None,
        output=None,
        status="completed",
        recipient=None,
        error=None,
        summary=None,
    ):
        # 늦은 응답은 삭제하지 않은 취소/이전 버전에만 보존한다. 호출부 중 반환값을
        # 쓰는 접수 경로는 새 ID 또는 현재 실행만 사용하므로 이 무기록 반환을 받지 않는다.
        if run_deleted(db, run_id):
            return None
        event = {
            "event_id": uid(),
            "run_id": run_id,
            "control_revision": revision,
            "seq": 0,
            "occurred_at": now(),
            "type": kind,
            "actor": actor,
            "recipient": recipient,
            "status": status,
            "summary": summary or kind,
            "input": copy.deepcopy(input),
            "output": copy.deepcopy(output),
            "error": error,
            "provider": self.provider,
        }
        cursor = db.execute(
            "INSERT INTO events(event_id,body) VALUES(?,?)", (event["event_id"], encode(event))
        )
        event["seq"] = cursor.lastrowid
        source = next(
            (
                v.get("source_mode")
                for v in (output, input)
                if isinstance(v, dict) and v.get("source_mode") in ("local", "web")
            ),
            None,
        )
        if source is None:
            row = db.execute(
                "SELECT body FROM revisions WHERE run_id=? AND revision=?", (run_id, revision)
            ).fetchone()
            source = json.loads(row[0]).get("source_mode", "local") if row else "local"
        event["source_mode"] = source
        if kind in {
            "instruction.received",
            "instruction.queued",
            "source_mode.changed",
        } and isinstance(event["output"], dict):
            event["output"]["state_seq"] = event["seq"]
        db.execute("UPDATE events SET body=? WHERE seq=?", (encode(event), event["seq"]))
        from 서버.활동기록 import attach_event

        attach_event(db, event)
        return event

    def save_revision(self, db, run):
        db.execute(
            "INSERT OR REPLACE INTO revisions VALUES(?,?,?)",
            (run["run_id"], run["control_revision"], encode(run)),
        )

    def duplicate(self, db, request_id, body):
        row = db.execute(
            "SELECT body,response FROM requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        if json.loads(row["body"]) != body:
            raise ApiError(409, "request_id_conflict", "요청 ID가 다른 내용에 사용되었습니다.")
        return json.loads(row["response"])

    def remember(self, db, request_id, body, response):
        db.execute(
            "INSERT INTO requests VALUES(?,?,?)", (request_id, encode(body), encode(response))
        )

    @staticmethod
    def summary(event):
        return {
            "source_mode": event.get("source_mode", "local"),
            **{k: v for k, v in event.items() if k not in {"input", "output", "error", "provider"}},
        }

    def snapshot(self):
        with self.read_connection() as db:
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            state.setdefault("selected_source_mode", "local")
            for record in state["runs"] + state["queue"]:
                record.setdefault("source_mode", "local")
            calls = db.execute("SELECT * FROM calls").fetchall()
            waiting = []
            for call in calls:
                if call["status"] == "waiting" and call["gate"]:
                    gate = json.loads(call["gate"])
                    gate["is_stale"] = not bool(
                        self.fenced(state, gate["run_id"], gate["control_revision"])
                    )
                    waiting.append(gate)
            rows = db.execute("SELECT body FROM events ORDER BY seq DESC LIMIT 100").fetchall()
            events = [self.summary(json.loads(row[0])) for row in reversed(rows)]
            watermark = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            return {
                **copy.deepcopy(state),
                "protocol_version": 1,
                "source_modes": list(self.source_modes),
                "mode": self.mode,
                "provider": self.provider,
                "waiting": waiting,
                "events": events,
                "events_has_more": db.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 100,
                "high_watermark_seq": watermark,
                "mock_requests": len(calls) if self.provider == "test-double" else 0,
                "actual_model_requests": sum(
                    run.get("actual_model_requests", 0) for run in state["runs"]
                ),
            }

    def normalize_legacy_spans(self, db, run_id, spans, events):
        """오염된 과거 실제 호출 링크만 메모리에서 보정한다. 원본 저널은 불변이다.

        시작 사건 ID·actor·revision·관측 시각·calls 행이 모두 일치하는 유일한
        대응만 허용한다. 단순 event_ids[0]/마지막 응답/행 순서는 근거가 아니다.
        """
        calls = {
            r["call_id"]: dict(r)
            for r in db.execute("SELECT * FROM calls WHERE run_id=?", (run_id,))
        }
        candidates = {}
        for span in spans:
            if span["kind"] != "model_wait" or span.get("call_id"):
                continue
            matches = set()
            for event in events:
                output = event.get("output")
                if (
                    event["provider"] != "openai"
                    or event["type"] != "model.started"
                    or event["event_id"] not in span["event_ids"]
                    or not isinstance(output, dict)
                ):
                    continue
                call_id = output.get("call_id")
                call = calls.get(call_id)
                if (
                    not call
                    or (call["actor"], call["revision"])
                    != (span["actor"], span["control_revision"])
                    or (event["actor"], event["control_revision"])
                    != (span["actor"], span["control_revision"])
                ):
                    continue
                try:
                    started = datetime.fromisoformat(span["started_at"])
                    occurred = datetime.fromisoformat(event["occurred_at"])
                    ended = datetime.fromisoformat(span["ended_at"]) if span["ended_at"] else None
                    if occurred < started or (ended is not None and occurred > ended):
                        continue
                except (ValueError, TypeError):
                    continue
                matches.add(call_id)
            candidates[span["span_id"]] = matches
        for span in spans:
            if span["span_id"] not in candidates:
                continue
            matches = candidates[span["span_id"]]
            if len(matches) != 1 or sum(matches == other for other in candidates.values()) != 1:
                span.update(event_ids=[], event_link_status="unavailable")
                continue
            call_id = next(iter(matches))
            call = calls[call_id]
            own = []
            for event in events:
                if (event["provider"], event["actor"], event["control_revision"]) != (
                    "openai",
                    span["actor"],
                    span["control_revision"],
                ) or event["type"] not in {
                    "model.started",
                    "model.completed",
                    "model.failed",
                    "result.superseded",
                }:
                    continue
                values = [event.get("output"), event.get("input")]
                if any(isinstance(v, dict) and v.get("call_id") == call_id for v in values):
                    own.append(event)
            span.update(call_id=call_id, event_ids=[e["event_id"] for e in own])
            # 늦은 응답의 적용 거절은 네트워크 실패가 아니다. 타이밍은 그대로 둔다.
            if any(
                e["type"] in {"model.completed", "result.superseded"}
                and isinstance(e["output"], dict)
                and "content" in e["output"]
                for e in own
            ):
                span["status"] = "completed"
            elif call["status"] in {"failed", "cancelled", "interrupted", "running", "superseded"}:
                span["status"] = call["status"]
            elif call["status"] == "completed":
                span["status"] = "completed"

    def archive(self, run_id):
        # 독립 읽기 전용 연결의 일관된 스냅샷. 실행 제어/모델/저널 쓰기 경로와 분리한다.
        with self.read_connection() as db:
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            run = self.run(state, run_id)
            if run is None:
                raise ApiError(404, "not_found", "실행이 없습니다.")
            revisions = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT body FROM revisions WHERE run_id=? ORDER BY revision", (run_id,)
                )
            ]
            events = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT body FROM events WHERE json_extract(body,'$.run_id')=? ORDER BY seq",
                    (run_id,),
                )
            ]
            run.setdefault("source_mode", "local")
            for event in events:
                event.setdefault("source_mode", "local")
            for revision in revisions:
                revision.setdefault("source_mode", "local")
            spans = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='activity_spans'").fetchone():
                spans = [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT body FROM activity_spans WHERE run_id=? ORDER BY rowid", (run_id,)
                    )
                ]
            if self.provider == "openai" and self.mode == "research":
                self.normalize_legacy_spans(db, run_id, spans, events)
            recorded = now()
            times = [run["created_at"], run["updated_at"]]
            times.extend(e["occurred_at"] for e in events)
            for span in spans:
                times.append(span["started_at"])
                if span["ended_at"]:
                    times.append(span["ended_at"])
                elif span["status"] == "running":
                    times.append(recorded)
            start = datetime.fromisoformat(run["created_at"])
            duration = max(
                0.0, max((datetime.fromisoformat(t) - start).total_seconds() * 1000 for t in times)
            )
            return {
                "protocol_version": 1,
                "provider": self.provider,
                "run": run,
                "revisions": revisions,
                "events": events,
                "spans": spans,
                "high_watermark_seq": max((e["seq"] for e in events), default=0),
                "started_at": run["created_at"],
                "ended_at": None if run["status"] == "running" else run["updated_at"],
                "recorded_at": recorded,
                "duration_ms": duration,
                "has_measured_spans": bool(spans),
                "truncated": False,
            }

    def get_event(self, event_id):
        with self.connection() as db:
            row = db.execute("SELECT body FROM events WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "사건이 없습니다.")
            event = json.loads(row[0])
            event.setdefault("source_mode", "local")
            return event

    def events_after(self, cursor):
        with self.connection() as db:
            return [
                self.summary(json.loads(r[0]))
                for r in db.execute(
                    "SELECT body FROM events WHERE seq>? ORDER BY seq LIMIT 200", (cursor,)
                )
            ]

    def restore(self):
        # 앱 저널 복원이며 LangGraph 체크포인터 재개가 아니다. 대역도 재호출하지 않는다.
        with self.transaction() as (db, state):
            # 다른 프로세스의 perf_counter 기준점은 복원할 수 없다. 종료시각/경과를 추정하지 않는다.
            for row in db.execute("SELECT span_id,body FROM activity_spans").fetchall():
                span = json.loads(row["body"])
                if span["status"] == "running":
                    span.update(
                        status="interrupted",
                        ended_at=None,
                        duration_ms=None,
                        timing_source="incomplete",
                    )
                    db.execute(
                        "UPDATE activity_spans SET body=? WHERE span_id=?",
                        (encode(span), row["span_id"]),
                    )
            interrupted = False
            for run in state["runs"]:
                if run["status"] == "running":
                    run.update(status="interrupted", phase="interrupted", updated_at=now())
                    self.save_revision(db, run)
                    self.event(
                        db,
                        run["run_id"],
                        run["control_revision"],
                        "run.interrupted",
                        "dispatcher",
                        output={"replayed_model_requests": 0},
                        status="interrupted",
                    )
                    interrupted = True
            count = db.execute(
                "SELECT COUNT(*) FROM calls WHERE status IN ('waiting','running','released')"
            ).fetchone()[0]
            db.execute(
                "UPDATE calls SET status='interrupted' WHERE status IN "
                "('waiting','running','released')"
            )
            if interrupted or count or state["queue"]:
                state.update(queue_paused=True, queue_pause_reason="interrupted")
