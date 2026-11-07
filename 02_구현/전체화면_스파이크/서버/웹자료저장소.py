"""명시 경로의 웹 출처 sidecar. 저널·모델 성공 여부와 revision 펜스는 호출자가 소유한다."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from 서버.웹계약 import WebSourceError

SOURCE_ERROR_CODES = frozenset(
    {
        "fetch_timeout",
        "fetch_failed",
        "search_timeout",
        "search_failed",
        "rate_limited",
        "challenge_detected",
        "cooldown_active",
        "http_error",
        "invalid_url",
        "unsafe_target",
        "dns_failed",
        "redirect_limit",
        "body_too_large",
        "unsupported_content_type",
        "extraction_failed",
        "empty_content",
        "observer_failed",
        "client_closed",
    }
)


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WebSourceStore:
    def __init__(self, path: Path):
        if not isinstance(path, Path):
            raise TypeError("explicit_path_required")
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS web_sources (
                    source_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    url TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(run_id, revision, url));
                CREATE TABLE IF NOT EXISTS web_history (
                    seq INTEGER PRIMARY KEY, source_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS web_reads (
                    seq INTEGER PRIMARY KEY, run_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    body TEXT NOT NULL);
            """)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _get(self, db, run_id, revision, source_id):
        row = db.execute(
            "SELECT body FROM web_sources WHERE run_id=? AND revision=? AND source_id=?",
            (run_id, revision, source_id),
        ).fetchone()
        if row is None:
            raise WebSourceError("source_not_found")
        value = json.loads(row[0])
        if value["status"] == "ready" and _hash(value["text"]) != value["text_sha256"]:
            raise WebSourceError("source_hash_mismatch")
        return value

    def register_candidates(self, run_id, revision, hits):
        result = []
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for hit in hits:
                row = db.execute(
                    "SELECT source_id FROM web_sources WHERE run_id=? AND revision=? AND url=?",
                    (run_id, revision, hit["url"]),
                ).fetchone()
                if row:
                    value = self._get(db, run_id, revision, row[0])
                else:
                    value = {
                        "source_id": uuid4().hex,
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
                    db.execute(
                        "INSERT INTO web_sources VALUES(?,?,?,?,?)",
                        (
                            value["source_id"],
                            run_id,
                            revision,
                            hit["url"],
                            json.dumps(value, ensure_ascii=False),
                        ),
                    )
                result.append(value)
        return result

    def _save(self, db, value, *, replace=True):
        body = json.dumps(value, ensure_ascii=False)
        db.execute(
            "INSERT INTO web_history(source_id,body) VALUES(?,?)", (value["source_id"], body)
        )
        if replace:
            db.execute(
                "UPDATE web_sources SET body=? WHERE source_id=?", (body, value["source_id"])
            )

    def save_fetch(self, run_id, revision, source_id, page):
        text = page["text"]
        if not isinstance(text, str) or _hash(text) != page["text_sha256"]:
            raise WebSourceError("source_hash_mismatch")
        if not text or len(text) > 200000:
            raise WebSourceError("invalid_source_text")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            value = self._get(db, run_id, revision, source_id)
            if page["requested_url"] != value["url"]:
                raise WebSourceError("source_url_mismatch")
            candidate = value | {
                key: page[key]
                for key in (
                    "final_url",
                    "title",
                    "text",
                    "text_sha256",
                    "html_sha256",
                    "fetched_at",
                    "extracted_date_candidate",
                    "warnings",
                )
            }
            candidate.update(status="ready", published_at=None, error_code=None)
            self._save(db, candidate, replace=value["status"] != "ready")
            return value if value["status"] == "ready" else candidate

    def mark_failed(self, run_id, revision, source_id, code):
        if code not in SOURCE_ERROR_CODES:
            raise WebSourceError("invalid_error_code")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            value = self._get(db, run_id, revision, source_id)
            self._save(
                db,
                value | {"status": "failed", "error_code": code},
                replace=value["status"] != "ready",
            )

    def mark_read(self, run_id, revision, source_id, section_id, model_call_id, read_text, note):
        # 성공한 앱 call ID만 전달할 책임은 저널을 소유한 통합부에 있다.
        if (
            not isinstance(model_call_id, str)
            or not model_call_id.strip()
            or not isinstance(section_id, str)
            or not section_id.strip()
        ):
            raise WebSourceError("invalid_read_receipt")
        if not isinstance(read_text, str) or not 0 < len(read_text) <= 20000:
            raise WebSourceError("invalid_read_text")
        if not isinstance(note, str) or len(note) > 6000:
            raise WebSourceError("invalid_read_note")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            value = self._get(db, run_id, revision, source_id)
            if value["status"] != "ready" or read_text != value["text"][: len(read_text)]:
                raise WebSourceError("invalid_read_text")
            claim = {
                "source_id": source_id,
                "section_id": section_id,
                "model_call_id": model_call_id,
                "read_text_sha256": _hash(read_text),
                "note_sha256": _hash(note),
            }
            db.execute(
                "INSERT INTO web_reads(run_id,revision,body) VALUES(?,?,?)",
                (run_id, revision, json.dumps(claim, ensure_ascii=False)),
            )

    def sources(self, run_id, revision):
        with self._connection() as db:
            return [
                self._get(db, run_id, revision, row[0])
                for row in db.execute(
                    "SELECT source_id FROM web_sources WHERE run_id=? AND revision=? ORDER BY rowid",
                    (run_id, revision),
                ).fetchall()
            ]

    def read_claims(self, run_id, revision):
        with self._connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM web_reads WHERE run_id=? AND revision=? ORDER BY seq",
                    (run_id, revision),
                )
            ]

    def get(self, run_id, revision, source_id):
        with self._connection() as db:
            return self._get(db, run_id, revision, source_id)

    @staticmethod
    def count_run_in(db, run_id):
        return {
            "web_sources": db.execute(
                "SELECT COUNT(*) FROM web_sources WHERE run_id=?", (run_id,)
            ).fetchone()[0],
            "web_reads": db.execute(
                "SELECT COUNT(*) FROM web_reads WHERE run_id=?", (run_id,)
            ).fetchone()[0],
        }

    def count_run(self, run_id):
        """실행 삭제 미리보기용 읽기 전용 개수. 아무것도 바꾸지 않는다."""
        with self._connection() as db:
            return self.count_run_in(db, run_id)

    def delete_run(self, run_id):
        """한 실행의 출처·읽음 기록과 그 출처에 딸린 이력을 한 트랜잭션으로 지운다.

        web_history에는 run_id 열이 없고 source_id로만 이어지므로, 출처 행을 지우기
        전에 이력을 먼저 지워야 연결 고리를 잃지 않는다.
        """
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            counts = self.count_run_in(db, run_id)
            db.execute(
                "DELETE FROM web_history WHERE source_id IN "
                "(SELECT source_id FROM web_sources WHERE run_id=?)",
                (run_id,),
            )
            db.execute("DELETE FROM web_sources WHERE run_id=?", (run_id,))
            db.execute("DELETE FROM web_reads WHERE run_id=?", (run_id,))
            return counts
