import hashlib
import json
import sqlite3

import pytest


def page(text="실제 본문", **changes):
    value = {
        "requested_url": "https://example.com/a",
        "final_url": "https://example.com/a",
        "title": "원문",
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "html_sha256": "a" * 64,
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "published_at": None,
        "extracted_date_candidate": "2001-01-01",
        "warnings": ["extraction_not_verified"],
        "elapsed_ms": 1.0,
        "observed_requests": 1,
    }
    return value | changes


def test_ready_is_verified_immutable_and_failed_attempt_is_retained(tmp_path):
    store = WebSourceStore(tmp_path / "sources.sqlite3")
    row = store.register_candidates(
        "run", 1, [{"title": "t", "url": "https://example.com/a", "snippet": "s"}]
    )[0]
    ready = store.save_fetch("run", 1, row["source_id"], page())
    assert ready["status"] == "ready"
    assert ready["published_at"] is None
    assert ready["extracted_date_candidate"] == "2001-01-01"
    assert store.save_fetch("run", 1, row["source_id"], page("다른 본문")) == ready
    with pytest.raises(WebSourceError, match="source_hash_mismatch"):
        store.save_fetch("run", 1, row["source_id"], page(text_sha256="0" * 64))
    store.mark_failed("run", 1, row["source_id"], "fetch_timeout")
    assert store.get("run", 1, row["source_id"]) == ready
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM web_history").fetchone()[0] == 3
        body = dict(ready, text="tampered")
        db.execute("UPDATE web_sources SET body=?", (json.dumps(body),))
    with pytest.raises(WebSourceError, match="source_hash_mismatch"):
        store.get("run", 1, row["source_id"])


from 서버.웹계약 import WebSourceError
from 서버.웹자료저장소 import WebSourceStore


def test_read_claim_requires_exact_prefix_and_app_receipt(tmp_path):
    store = WebSourceStore(tmp_path / "sources.sqlite3")
    row = store.register_candidates(
        "run", 1, [{"title": "t", "url": "https://example.com/a", "snippet": "s"}]
    )[0]
    sid = row["source_id"]
    store.save_fetch("run", 1, sid, page("a" * 21000))
    assert store.read_claims("run", 1) == []
    for read, note, call, code in [
        ("b", "", "app-call", "invalid_read_text"),
        ("a" * 20001, "", "app-call", "invalid_read_text"),
        ("", "", "app-call", "invalid_read_text"),
        ("a", "n" * 6001, "app-call", "invalid_read_note"),
        ("a", "", "", "invalid_read_receipt"),
    ]:
        with pytest.raises(WebSourceError, match=code):
            store.mark_read("run", 1, sid, "section", call, read, note)
        assert store.read_claims("run", 1) == []
    store.mark_read("run", 1, sid, "section", "app-success-id", "a" * 20000, "n" * 6000)
    claims = store.read_claims("run", 1)
    assert claims == [
        {
            "source_id": sid,
            "section_id": "section",
            "model_call_id": "app-success-id",
            "read_text_sha256": hashlib.sha256(("a" * 20000).encode()).hexdigest(),
            "note_sha256": hashlib.sha256(("n" * 6000).encode()).hexdigest(),
        }
    ]
    assert len(store.sources("run", 1)) == 1
    assert store.read_claims("run", 2) == []


def test_candidates_are_scoped_and_url_not_title_is_identity(tmp_path):
    store = WebSourceStore(tmp_path / "sources.sqlite3")
    hits = [
        {"title": "같은 제목", "url": "https://example.com/a", "snippet": "a"},
        {"title": "같은 제목", "url": "https://example.com/b", "snippet": "b"},
    ]
    rows = store.register_candidates("run", 1, hits)
    assert len(rows) == 2
    assert rows[0]["source_id"] != rows[1]["source_id"]
    assert store.register_candidates("run", 1, hits) == rows
    assert store.register_candidates("run", 2, hits)[0]["source_id"] != rows[0]["source_id"]
    with pytest.raises(WebSourceError, match="source_not_found"):
        store.get("other", 1, rows[0]["source_id"])
