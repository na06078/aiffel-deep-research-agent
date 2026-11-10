"""보존된 실제 실패를 새 문맥 요청/파서 경계에 무호출로 재생한다.

원고/원 판정/DB는 수정하지 않는다. 주입 판정은 전달·차단 계약 검사이며
실제 모델의 사실 판별 또는 해당 역사 문장의 최종 합격 증거가 아니다.
"""

import hashlib
import json
import socket
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

I = Path(__file__).resolve().parents[1]
R = I.parents[1]
sys.path.insert(0, str(I))
from 서버.검토문맥 import build_review_request, parse_review
from 서버.교안연구 import ResearchContractError
from 서버.보고서품질 import parse_semantic

ORIGINAL = R / "03_실행데이터/보고서품질_평면실제연구/실행기록.sqlite3"
CORPUS = R / "01_참고자료/원본데이터/corpus.json"
OUT = I / "검증결과/근거문맥/저장실패_재생" / uuid.uuid4().hex
CACHE = R / ".cache/보고서품질_검증/실모델점검/근거문맥/저장재생" / OUT.name


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    CACHE.mkdir(parents=True, exist_ok=False)
    protected = {str(path): digest(path) for path in [ORIGINAL, CORPUS]}
    proof = {
        "passed": False,
        "checks": [],
        "actual_provider_requests": 0,
        "injected_decisions_not_model_judgment": True,
        "scope": "stored original input through current pure request/parser only; no database writes or publication",
        "unverified": ["Luna 의미 판별력", "원문 밖 역사적 진실", "새 전체 실제 연구 성공"],
    }

    def check(name, condition, **facts):
        proof["checks"].append({"name": name, "passed": bool(condition), **facts})
        if not condition:
            raise AssertionError(name)

    try:
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))["docs"]
        with closing(sqlite3.connect(ORIGINAL.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            revisions = {
                revision: json.loads(body)
                for revision, body in db.execute(
                    "SELECT revision,body FROM revisions ORDER BY revision"
                )
            }
            events = [
                json.loads(body) for (body,) in db.execute("SELECT body FROM events ORDER BY seq")
            ]
        original_sections = [
            event["output"] for event in events if event["type"] == "research.completed"
        ]
        correction = next(
            event["output"] for event in events if event["type"] == "report.correction.applied"
        )
        by_revision = {1: original_sections, 2: correction["sections"]}
        check(
            "original r1/r2 remain blocked without final report",
            set(revisions) == {1, 2}
            and all(
                run["status"] == "blocked" and run["report_id"] is None
                for run in revisions.values()
            ),
        )

        requests = {}
        with (
            patch.object(socket, "socket", side_effect=AssertionError("network forbidden")),
            patch.object(subprocess, "Popen", side_effect=AssertionError("process forbidden")),
        ):
            for revision, sections in by_revision.items():
                qualified = {
                    s["section_id"]: {source: corpus[source] for source in s["읽은문서"]}
                    for s in sections
                }
                request = build_review_request(revisions[revision]["draft"], sections, qualified)
                requests[revision] = (request, qualified)
                metadata = request.inputs
                check(
                    f"r{revision}: real qualified input keeps exact context slices",
                    bool(metadata["contexts"])
                    and all(
                        c["text"]
                        == qualified[c["section_id"]][c["source_id"]][c["start"] : c["end"]]
                        and c["source_hash"]
                        == hashlib.sha256(
                            qualified[c["section_id"]][c["source_id"]].encode()
                        ).hexdigest()
                        for c in metadata["contexts"]
                    ),
                )
                check(
                    f"r{revision}: actual serialized request is bounded",
                    sum(len(part.encode()) for part in request.prompts)
                    + len(
                        json.dumps(
                            request.response_schema, ensure_ascii=False, allow_nan=False
                        ).encode()
                    )
                    + 8192
                    <= 272000,
                )
                (CACHE / f"수정버전{revision}_새검토입력.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                # 과거 모델 응답은 새 계약의 통과 응답으로 재해석하지 않는다.
                old = revisions[revision]["quality"]["semantic_review_raw"]
                read_ids = {source for values in qualified.values() for source in values}
                check(
                    f"r{revision}: historical verdict still readable by legacy parser",
                    parse_semantic(old, read_ids)["통과"] is False,
                )
                try:
                    parse_review(old, request, qualified)
                except ResearchContractError:
                    check(f"r{revision}: old boolean-only response rejected at new boundary", True)
                else:
                    raise AssertionError("legacy response accepted as current review")

            cases = [
                (
                    2,
                    "만석보",
                    "만석보를 파괴",
                    "1894년 음력 1월 고부에서 전봉준이 이끄는 농민군은 만석보를 파괴하고 관아를 공격했으며",
                ),
                (
                    1,
                    "강화조건",
                    "정부는 강화 조건을 이행하지 않는",
                    "그러나 정부가 화약의 개혁 조건을 제대로 이행하지 않고 일본이 경복궁을 점령하자",
                ),
            ]
            for revision, label, anchor, excerpt in cases:
                request, qualified = requests[revision]
                contexts = request.inputs["contexts"]
                candidates = [
                    c
                    for c in contexts
                    if c["source_id"] == "동학 농민 혁명"
                    and anchor in c["text"]
                    and c["kind"] != "quote"
                ]
                check(
                    label + ": omitted support is actually offered as original context",
                    bool(candidates),
                    offered_contexts=len(contexts),
                    matching_ids=[c["id"] for c in candidates],
                )
                context = candidates[0]
                token = request.response_schema["properties"]["검토목록ID"]["enum"][0]
                raw = {
                    "검토목록ID": token,
                    "판정": "needs_revision",
                    "문제": [
                        {
                            "유형": "citation_gap",
                            "설명": "주입한 선택 누락 판정; 모델 판별 증거 아님",
                            "본문발췌": excerpt,
                            "근거번호": [context["id"]],
                        }
                    ],
                    "검토요약": "검증된 다른 구간을 보이되 원래 인용을 자동 통과시키지 않는다.",
                }
                decision = parse_review(json.dumps(raw, ensure_ascii=False), request, qualified)
                check(
                    label + ": citation gap remains blocked with bound support",
                    decision["통과"] is False
                    and decision["문제"][0]["유형"] == "citation_gap"
                    and decision["문제"][0]["근거문맥"][0]["text"] == context["text"],
                )
                (CACHE / f"{label}_주입판정.json").write_text(
                    json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                for change in ["request", "source", "excerpt"]:
                    malformed = json.loads(json.dumps(raw, ensure_ascii=False))
                    changed_sources = {sid: dict(values) for sid, values in qualified.items()}
                    if change == "request":
                        malformed["검토목록ID"] = "another-request"
                    elif change == "source":
                        changed_sources[context["section_id"]][context["source_id"]] += "원문 변경"
                    else:
                        malformed["문제"][0]["본문발췌"] = "실제 원고에는 없는 발췌를 지적하는 오류"
                    try:
                        parse_review(
                            json.dumps(malformed, ensure_ascii=False), request, changed_sources
                        )
                    except ResearchContractError as error:
                        check(label + ": reject forged " + change, True, error=str(error))
                    else:
                        raise AssertionError("forged review accepted: " + change)

            # 단어가 원문에 있다는 이유만으로 과거 판정을 supported로 올리지 않는다.
            check(
                "chronology and mixed-event original objections are preserved",
                "시간 순서" in revisions[1]["quality"]["semantic_review_raw"]
                and "백산" in revisions[2]["quality"]["semantic_review_raw"],
            )
        proof["passed"] = True
    except Exception as error:  # noqa: BLE001 - 공개 보존 자료 재생의 실패를 원형 기록하는 최외곽 경계
        proof["error"] = f"{type(error).__name__}: {error}"
    finally:
        after = {str(path): digest(path) for path in [ORIGINAL, CORPUS]}
        proof["original_hashes_unchanged"] = protected == after
        proof["original_sha256"] = protected
        proof["passed"] = proof["passed"] and protected == after
        proof["checked"] = len(proof["checks"])
        (OUT / "확인.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "passed": proof["passed"],
                    "checks": proof["checked"],
                    "evidence": str(OUT / "확인.json"),
                    "error": proof.get("error"),
                },
                ensure_ascii=False,
            )
        )
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
