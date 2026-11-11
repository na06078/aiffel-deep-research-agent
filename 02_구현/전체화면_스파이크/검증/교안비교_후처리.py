"""저장된 실제 읽기 사건의 진단 지표. 연구/품질 차단/원자료를 수정하지 않는다."""

import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    archive = json.loads(args.archive.read_text("utf-8"))
    print(json.dumps(reading_metrics(archive), ensure_ascii=False, indent=2))
    return 0


def reading_metrics(archive):
    """절별 증거 접근권과 조사자의 실제 읽기를 구별한다."""
    revision = archive["run"]["control_revision"]
    events = [e for e in archive["events"] if e["control_revision"] == revision]
    completed = {
        e["output"]["call_id"]
        for e in events
        if e["type"] == "model.completed" and e["output"]["content"].strip()
    }
    started = [
        e
        for e in events
        if e["type"] == "model.started" and e["input"]["context"]["operation"] == "read_one"
    ]
    reads = [e for e in started if e["output"]["call_id"] in completed]
    pairs = {(e["actor"], e["input"]["inputs"]["document"]) for e in reads}
    documents = {document for _, document in pairs}
    return {
        "읽기시도": len(started),
        "완료읽기": len(reads),
        "고유문서": len(documents),
        "조사자문서쌍": len(pairs),
        "중복률": round(100 * (1 - len(documents) / len(pairs)), 1) if pairs else None,
        "재읽기율": round(100 * (1 - len(documents) / len(reads)), 1) if reads else None,
    }


if __name__ == "__main__":
    raise SystemExit(main())
