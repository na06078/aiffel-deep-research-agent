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


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class Repository:
    def __init__(self, data_dir: Path):
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
            """)
            initial = {
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
            "provider": "test-double",
        }
        cursor = db.execute(
            "INSERT INTO events(event_id,body) VALUES(?,?)", (event["event_id"], encode(event))
        )
        event["seq"] = cursor.lastrowid
        db.execute("UPDATE events SET body=? WHERE seq=?", (encode(event), event["seq"]))
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
        return {k: v for k, v in event.items() if k not in {"input", "output", "error", "provider"}}

    def snapshot(self):
        with self.transaction() as (db, state):
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
                "mode": "spike",
                "provider": "test-double",
                "waiting": waiting,
                "events": events,
                "events_has_more": db.execute("SELECT COUNT(*) FROM events").fetchone()[0] > 100,
                "high_watermark_seq": watermark,
                "mock_requests": len(calls),
                "actual_model_requests": 0,
            }

    def get_event(self, event_id):
        with self.connection() as db:
            row = db.execute("SELECT body FROM events WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "사건이 없습니다.")
            return json.loads(row[0])

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
