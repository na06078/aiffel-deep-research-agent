"""실제 기존 연구의 모델 구간·사건 연결과 순수 GET 불변 검증."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = "http://127.0.0.1:8768"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.load(response)


def main(label):
    data = ROOT / "03_실행데이터/실제연구/실행기록.sqlite3"
    before_hash = hashlib.sha256(data.read_bytes()).hexdigest()
    snapshot = get("/api/spike/snapshot")
    run = next(r for r in snapshot["runs"] if r["run_id"] == snapshot["current_run_id"])
    archive = get(f"/api/spike/runs/{run['run_id']}/archive")
    events = {event["event_id"]: event for event in archive["events"]}
    problems = []
    spans = [span for span in archive["spans"] if span["kind"] == "model_wait"]
    allowed = {"model.started", "model.completed", "model.failed", "result.superseded"}
    for span in spans:
        linked = [events[event_id] for event_id in span["event_ids"] if event_id in events]
        call_ids = set()
        for event in linked:
            if event["type"] not in allowed or event["actor"] != span["actor"]:
                problems.append(
                    {
                        "span_id": span["span_id"],
                        "reason": "unrelated_event",
                        "event_id": event["event_id"],
                        "event_type": event["type"],
                    }
                )
            output, inputs = event.get("output"), event.get("input")
            call_id = output.get("call_id") if isinstance(output, dict) else None
            if call_id is None and isinstance(inputs, dict):
                call_id = inputs.get("call_id")
            if call_id:
                call_ids.add(call_id)
        if len(call_ids) != 1:
            problems.append(
                {
                    "span_id": span["span_id"],
                    "reason": "not_one_call",
                    "linked_call_count": len(call_ids),
                }
            )
        if span.get("call_id") and call_ids != {span["call_id"]}:
            problems.append({"span_id": span["span_id"], "reason": "call_id_mismatch"})
    after = get("/api/spike/snapshot")
    report = get(f"/api/spike/reports/{run['report_id']}")
    result = {
        "status": "PASS" if not problems else "FAIL",
        "run_id": run["run_id"],
        "model_spans": len(spans),
        "problems": problems,
        "db_unchanged": before_hash == hashlib.sha256(data.read_bytes()).hexdigest(),
        "budget_unchanged": snapshot["budget"] == after["budget"],
        "actual_requests": after["budget"]["attempt_count"],
        "report_sha256": report["sha256"],
        "scope": "실제 기존 연구 기록의 읽기 전용 API 검사",
    }
    output = ROOT / "02_구현/전체화면_스파이크/검증결과" / f"실제연구_기록연결_{label}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "problems"},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    assert not problems, f"구간/사건 혼입 {len(problems)}건"
    assert result["db_unchanged"] and result["budget_unchanged"]
    assert report["sha256"] == "19c6f5f980533a7e001beecc6f3f2852f44869800c4ad0a7afd3dea0e4d242cd"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="최종")
    args = parser.parse_args()
    main(args.label)
