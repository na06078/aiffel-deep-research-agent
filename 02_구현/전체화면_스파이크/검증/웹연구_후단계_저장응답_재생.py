"""웹 연구 1차 실행(failed)의 저장 데이터로 후단계를 무비용 재생한다.

1차 실행은 4번째 절의 읽기에서 멈췄다. 그 뒤 단계(읽기 receipt 검증·원고 조립·품질 평가)가 실제
저장 데이터에서 예외 없이 도는지 새 실제 실행 전에 확인한다. 데이터는 복사본에서만 열며 모델·검색·
본문 요청은 하지 않는다. 원본 폴더는 복사 전후 해시로 불변을 확인한다.

사용: 격리 서버를 먼저 정지한 뒤 실행한다.
  python 검증/웹연구_후단계_저장응답_재생.py --run-id <run_id> --label <폴더 이름>
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SPIKE))

from 서버 import 웹연구
from 서버.교안연구 import evaluate_report
from 서버.실제연구제어 import RealResearchService

CORPUS = ROOT / "01_참고자료/원본데이터/corpus.json"
SOURCE_DATA = ROOT / "03_실행데이터/웹연구_검증"
REPLAY_ROOT = ROOT / ".cache/웹결함수정_재생"
EVIDENCE = SPIKE / "검증결과"
FIXED_INTRODUCTION = "머리말: 재생 점검용 고정 문장입니다."
FIXED_CONCLUSION = "맺음말: 재생 점검용 고정 문장입니다."


def file_hashes(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


class _ForbiddenModel:
    """재생은 모델을 호출하지 않는다. 호출하면 즉시 실패해 그 사실을 보장한다."""

    async def complete(self, *args, **kwargs):
        raise AssertionError("replay_must_not_call_model")

    async def aclose(self):
        return None


async def replay(copy: Path, run_id: str) -> dict:
    service = RealResearchService(copy, _ForbiddenModel(), CORPUS, web_client=None)
    # 끝난(failed) 실행은 활성 실행 검사를 통과하지 못하므로 이 재생에서만 건너뛴다.
    service.check = lambda state: None
    try:
        events = service.repo.archive(run_id)["events"]
        completed = sorted(
            (e["output"] for e in events if e["type"] == "research.completed"),
            key=lambda section: section["section_id"],
        )
        state = {"run_id": run_id, "revision": 1}
        corpus, verified, sources = 웹연구.evidence(service, state, completed)
        per_section = [
            {
                "section_id": original["section_id"],
                "claimed_reads": len(original["읽은문서"]),
                "verified_reads": len(checked["읽은문서"]),
                "same_reads": sorted(original["읽은문서"]) == checked["읽은문서"],
            }
            for original, checked in zip(completed, verified, strict=True)
        ]
        accepted = [section for section in verified if section["읽은문서"]]
        draft = 웹연구.assemble(
            "재생 점검", FIXED_INTRODUCTION, FIXED_CONCLUSION, accepted, sources
        )
        quality = evaluate_report(draft, accepted, corpus)
    finally:
        await service.close()
    return {
        "sections": per_section,
        "accepted_sections": len(accepted),
        "provenance_sources": len({d for s in accepted for d in s["읽은문서"]}),
        "draft_characters": len(draft),
        "draft_sha256": hashlib.sha256(draft.encode()).hexdigest(),
        "quality_passed": quality["passed"],
        "quality_reasons": quality["reasons"],
        "unknown_citations": quality["unknown_citations"],
        "unread_citations": quality["unread_citations"],
        "misattributed_citations": quality["misattributed_citations"],
        "metrics": quality["metrics"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--label", required=True, help=".cache/웹결함수정_재생 아래 복사본 폴더 이름"
    )
    args = parser.parse_args()
    copy = REPLAY_ROOT / args.label / "데이터"
    before = file_hashes(SOURCE_DATA)
    shutil.copytree(SOURCE_DATA, copy)  # 대상이 이미 있으면 덮어쓰지 않고 멈춘다.
    result = asyncio.run(replay(copy, args.run_id))
    result["original_unchanged"] = file_hashes(SOURCE_DATA) == before
    result["external_requests"] = {"model": 0, "search": 0, "fetch": 0}
    result["run_id"] = args.run_id
    out = EVIDENCE / "웹연구_후단계_재생_결과.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    ok = (
        result["original_unchanged"]
        and all(s["same_reads"] for s in result["sections"])
        and result["accepted_sections"] > 0
    )
    print("REPLAY_" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
