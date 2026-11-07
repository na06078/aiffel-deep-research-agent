"""웹 초안 프롬프트를 실제 모델로 4회만 점검한다(1차 실행에 저장된 실제 메모 사용).

점검 대상: `웹연구.web_section_prompt`가 실제 모델에게 «source_id»를 글자 그대로 인용하게 하는가.
1차 실행에서는 로컬용 지시 때문에 4개 절 모두 메모 제목을 인용했다.
- 기본은 드라이런이다: 프롬프트만 만들어 크기를 보이고 `.env`·네트워크·장부를 건드리지 않는다.
- `--confirm`을 줘야 실제 모델을 4회 호출한다(절당 1회, 순차). 호출은 앱과 같은 `OpenAIModel`과
  공유 예산 장부를 거치므로 장부의 100회/$2 한도가 그대로 적용된다.
- 실행 예약 파일을 배타 생성하므로 같은 점검은 한 번만 실행된다(재실행은 거절).
응답 본문은 Git 밖 `.cache/웹초안점검/`에만 저장하고, 증거 JSON에는 수치·인용 목록만 남긴다.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
sys.path.insert(0, str(SPIKE))

from 서버.교안연구 import evaluate_report, parse_section
from 서버.웹연구 import web_section_prompt

RUN_ID = "ba66f9e7a4ff48c5b6cecfe80060cd92"
SOURCE_DB = ROOT / ".cache/웹결함수정_재생/1차실행_후단계_재시도/데이터/실행기록.sqlite3"
LEDGER = ROOT / "03_실행데이터/실제연구/호출예산.sqlite3"
ENV_FILE = ROOT / ".env"
WORK = ROOT / ".cache/웹초안점검"
RESULT = SPIKE / "검증결과/웹연구_초안프롬프트_점검_결과.json"
MAX_CALLS = 4


def load_sections():
    """1차 실행의 절별 (배정, 메모 목록)을 복사본 DB에서 읽기 전용으로 꺼낸다."""
    db = sqlite3.connect(SOURCE_DB.resolve().as_uri() + "?mode=ro", uri=True)
    with closing(db):
        events = [json.loads(r[0]) for r in db.execute("SELECT body FROM events ORDER BY seq")]
    events = [e for e in events if e.get("run_id") == RUN_ID]
    completed = {
        e["output"]["section_id"]: e["output"] for e in events if e["type"] == "research.completed"
    }
    assignments = {}
    for event in events:
        if event["type"] == "assignment.created":
            assignments[event["output"]["section_id"]] = event[
                "output"
            ]  # 마지막 배정(2라운드 지시 포함)
    notes = {sid: out["메모"] for sid, out in completed.items() if out["메모"]}
    for sid in assignments:
        if sid not in notes:
            # 절 4는 1라운드 읽기가 0건이라, 2라운드에서 성공한 읽기의 메모(tool.completed)를 쓴다.
            notes[sid] = [
                [e["output"]["source_id"], e["output"]["note"]]
                for e in events
                if e["type"] == "tool.completed"
                and (e.get("input") or {}).get("section_id") == sid
                and "note" in (e.get("output") or {})
            ]
    sections = []
    for sid in sorted(assignments):
        assignment = {
            k: assignments[sid][k] for k in ("section_id", "title", "instruction", "role")
        }
        sections.append((assignment, notes[sid]))
    return sections


def analyze(assignment, notes, raw):
    ids = [n[0] for n in notes]
    try:
        parsed = parse_section(raw)
    except Exception as error:  # noqa: BLE001 -- 형식 오류도 점검 결과로 남긴다.
        return {
            "parsed": False,
            "error": type(error).__name__ + ": " + str(error)[:160],
            "section": None,
        }
    citations = re.findall(r"«([^»]+)»", parsed["본문"])
    return {
        "parsed": True,
        "sufficient": parsed["충분"],
        "body_characters": len(parsed["본문"]),
        "citation_count": len(citations),
        "valid_citation_count": sum(c in ids for c in citations),
        "unknown_citations": sorted(set(citations) - set(ids)),
        "source_ids": ids,
        "section": {**assignment, **parsed, "읽은문서": ids},
    }


async def call_model(sections):
    from 서버.실제모델 import OpenAIModel

    WORK.mkdir(parents=True, exist_ok=True)
    reservation = WORK / "실행시작.json"
    with reservation.open("x", encoding="utf-8") as handle:  # 재실행이면 FileExistsError로 멈춘다.
        json.dump({"started_at": datetime.now(UTC).isoformat(), "max_calls": MAX_CALLS}, handle)
    model = OpenAIModel(ENV_FILE, LEDGER)
    results, bodies = [], {}
    before = model.usage()
    try:
        for assignment, notes in sections:
            system, user = web_section_prompt(assignment, notes)
            response = await model.complete(
                system,
                user,
                context={
                    "run_id": "probe-" + RUN_ID[:8],
                    "revision": 1,
                    "actor": "probe",
                    "operation": "draft_section",
                },
                max_output_tokens=4096,
                json_mode=True,
            )
            analysis = analyze(assignment, notes, response["content"])
            bodies[assignment["section_id"]] = response["content"]
            results.append(
                {
                    "section_id": assignment["section_id"],
                    "notes": len(notes),
                    "prompt_sha256": hashlib.sha256((system + "\n" + user).encode()).hexdigest(),
                    "usage": response["usage"],
                    "estimated_cost_usd": response["estimated_cost_usd"],
                    **{k: v for k, v in analysis.items() if k != "section"},
                    "_section": analysis["section"],
                }
            )
    finally:
        after = model.usage()
        await model.aclose()
    (WORK / "응답본문.json").write_text(
        json.dumps(bodies, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return results, before, after


def judge(results):
    sections = [r["_section"] for r in results if r.get("_section")]
    all_ids = {sid for s in sections for sid in s["읽은문서"]}
    report = (
        "# 초안 점검\n머리말(점검용)\n\n"
        + "\n\n".join(f"## {i + 1}. {s['title']}\n\n{s['본문']}" for i, s in enumerate(sections))
        + "\n\n## 출처 및 이용허락\n점검용"
    )
    quality = evaluate_report(report, sections, SimpleNamespace(docs=dict.fromkeys(all_ids, "")))
    per_section_ok = all(
        r["parsed"] and r["valid_citation_count"] >= 1 and not r["unknown_citations"]
        for r in results
    )
    return {
        "probe_passed": per_section_ok
        and len(results) == MAX_CALLS
        and not quality["unknown_citations"]
        and not quality["unread_citations"]
        and not quality["misattributed_citations"],
        "criteria": "4개 절 모두 JSON 형식·절마다 유효 인용 1개 이상·자기 절 source_id 밖 인용 0",
        "quality": {
            k: quality[k]
            for k in (
                "passed",
                "reasons",
                "unknown_citations",
                "unread_citations",
                "misattributed_citations",
            )
        },
        "metrics": quality["metrics"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--confirm", action="store_true", help="실제 모델을 4회 호출한다")
    args = parser.parse_args()
    sections = load_sections()
    assert len(sections) == MAX_CALLS, f"절 {len(sections)}개(예상 {MAX_CALLS})"
    for assignment, notes in sections:
        system, user = web_section_prompt(assignment, notes)
        print(
            f"{assignment['section_id']}: 메모 {len(notes)}건 · 프롬프트 {len(system) + len(user)}자 · 예시 id {notes[0][0][:8]}…"
        )
    if not args.confirm:
        print("DRY_RUN: 모델·네트워크·장부·.env 접근 없음. 실제 점검은 --confirm")
        return 0
    results, before, after = asyncio.run(call_model(sections))
    verdict = judge(results)
    public = [{k: v for k, v in r.items() if k != "_section"} for r in results]
    record = {
        "ledger_before": {
            k: before[k] for k in ("attempt_count", "remaining_attempts", "spent_usd")
        },
        "ledger_after": {k: after[k] for k in ("attempt_count", "remaining_attempts", "spent_usd")},
        "calls": len(results),
        "spent_usd_this_probe": round(after["spent_usd"] - before["spent_usd"], 6),
        "sections": public,
        **verdict,
    }
    RESULT.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                k: record[k]
                for k in (
                    "ledger_before",
                    "ledger_after",
                    "calls",
                    "spent_usd_this_probe",
                    "probe_passed",
                    "quality",
                )
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    for r in public:
        print(
            r["section_id"],
            "parsed" if r["parsed"] else "PARSE_FAIL",
            "citations",
            r.get("citation_count"),
            "valid",
            r.get("valid_citation_count"),
            "unknown",
            r.get("unknown_citations"),
        )
    print("PROBE_" + ("PASS" if record["probe_passed"] else "FAIL"))
    return 0 if record["probe_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
