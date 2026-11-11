"""단일 조건 1회 비교 실행기. 기본/check/prepare/adopt는 제공자를 생성하지 않는다.

예: python 검증/비교실험_실행.py --check --ledger <기존장부>
    ... --prepare --ledger <기존장부> --label 제출01
    ... --execute --ledger <기존장부> --label 제출01 --arm single
전체 조건은 --arm all. --resume은 이미 결과가 있는 실패도 재실행하지 않는다.
기준 재사용: 같은 ExperimentConfig baseline으로 실행한 repo.archive(run_id)에
완료 시 report_bytes(report_id)[0]을 report 키로 넣어 JSON 저장한 뒤
--adopt-baseline <그JSON> --ledger ... --label ... 를 실행한다.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

IMPLEMENTATION = Path(__file__).resolve().parents[1]
if str(IMPLEMENTATION) not in sys.path:
    sys.path.insert(0, str(IMPLEMENTATION))
from 서버.실험설정 import ARM_NOTES, ARMS, source_manifest

ROOT = IMPLEMENTATION.parents[1]
CORPUS = ROOT / "01_참고자료/원본데이터/corpus.json"
QUESTION = "1894년에 일어난 주요 사건들은 무엇이고 각각의 원인은 무엇인가?"
QUESTION_PRESETS = {
    "Q1": "운요호 사건은 무엇인가?",
    "Q4": QUESTION,
    "Q6": "김옥균이 관여한 사건들과 그 결과는 무엇인가?",
}
MODEL_ID = "gpt-5.6-luna"
PROTOCOL = "controlled-research-comparison-v1"


def stamp():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".pending")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def within(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("comparison: path outside approved root")
    return path


def locations(label):
    if not re.fullmatch(r"[A-Za-z0-9가-힣_-]{1,64}", label):
        raise ValueError("comparison: invalid label")
    return {
        "data": ROOT / "03_실행데이터/제출_비교실험" / label,
        "cache": ROOT / ".cache/보고서품질_검증/실모델점검/제출_비교" / label,
        "evidence": IMPLEMENTATION / "검증결과/제출/비교" / label,
    }


def ledger_snapshot(path):
    """기존 DB 읽기만. CallBudget 생성/정책 변경/unknown 정산을 하지 않는다."""
    path = within(path, ROOT)
    if not path.is_file():
        raise ValueError("comparison: existing ledger required")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        policy = db.execute(
            "SELECT max_attempts,max_nano_usd,reset_after_rowid FROM budget_policy WHERE id=1"
        ).fetchone()
        if policy is None or policy[:2] != (10000, 20000000000):
            raise ValueError("comparison: policy must remain 10000/$20")
        rows = db.execute(
            "SELECT rowid,id,reserved,spent,input_tokens,output_tokens FROM attempts ORDER BY rowid"
        ).fetchall()
    if type(policy[2]) is not int or not 0 <= policy[2] <= max((r[0] for r in rows), default=0):
        raise ValueError("comparison: invalid ledger baseline")
    return {
        "path": str(path),
        "policy": list(policy),
        "row_count": len(rows),
        "row_hashes": {r[1]: digest(canonical(r).encode()) for r in rows},
        "unknown_ids": [r[1] for r in rows if r[3] is None],
        "attempt_count": sum(r[0] > policy[2] for r in rows),
        "spent_nano_usd": sum(r[3] or 0 for r in rows),
        "reserved_nano_usd": sum(r[2] for r in rows if r[3] is None),
        "input_tokens": sum(r[4] or 0 for r in rows),
        "output_tokens": sum(r[5] or 0 for r in rows),
    }


def preserved(before, after):
    return (
        before["policy"] == after["policy"]
        and all(
            after["row_hashes"].get(key) == value for key, value in before["row_hashes"].items()
        )
        and set(before["unknown_ids"]) <= set(after["unknown_ids"])
    )


def identity(corpus=CORPUS, *, question=QUESTION):
    corpus = Path(corpus)
    data = json.loads(corpus.read_text("utf-8"))
    if len(data.get("docs", {})) != 34:
        raise ValueError("comparison: fixed 34-document corpus required")
    return {
        "protocol": PROTOCOL,
        "question": question,
        "question_key": next(
            (key for key, text in QUESTION_PRESETS.items() if text == question), None
        ),
        "model_id": MODEL_ID,
        "provider_settings": {
            "reasoning_effort": "low",
            "max_completion_tokens": 4096,
            "temperature": "omitted: provider default (not controlled)",
            "seed": "omitted: not fixed",
            "input_proxy_limit_bytes": 272000,
            "sdk_automatic_retries": 0,
            "draft_evidence_max_spans": 128,
            "draft_evidence_max_utf8_bytes": 96000,
        },
        "corpus_sha256": digest(corpus.read_bytes()),
        "source_hashes": source_manifest(),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "arms": {key: value.as_dict() for key, value in ARMS.items()},
        "notes": dict(ARM_NOTES),
        "statistical_superiority_claimed": False,
        "max_actual_requests_per_arm": 55,
        "automatic_retry": False,
    }


class CappedModel:
    """55번째까지 보수적으로 예약한 뒤 forwarding. 동시 호출/오류도 재시도 안 함."""

    def __init__(self, model, limit=55):
        if type(limit) is not int or not 1 <= limit <= 55:
            raise ValueError("comparison: cap must be 1..55")
        self.model, self.limit = model, limit
        self.forwarded = 0
        self.actual_attempts = 0
        self.lock = asyncio.Lock()

    async def complete(self, system, user, *, on_attempt, **kwargs):
        from 서버.실제모델 import ModelCallError

        async with self.lock:
            if self.forwarded >= self.limit:
                raise ModelCallError(
                    "experiment_request_limit", "실험별 55요청 상한으로 차단했습니다."
                )
            self.forwarded += 1
        sent = False

        def count(attempt_id):
            nonlocal sent
            if sent:
                raise ModelCallError("unexpected_retry", "자동 재시도를 허용하지 않습니다.")
            sent = True
            self.actual_attempts += 1
            on_attempt(attempt_id)

        return await self.model.complete(system, user, on_attempt=count, **kwargs)

    def usage(self):
        return self.model.usage()

    async def aclose(self):
        await self.model.aclose()


def summarize(archive, config):
    run, events = archive["run"], archive["events"]
    started = [e for e in events if e["type"] == "model.started"]
    completed = [e for e in events if e["type"] == "model.completed"]
    reads = [
        e["input"]["inputs"]["document"]
        for e in started
        if e["input"]["context"]["operation"] == "read_one"
    ]
    models = sorted({e["output"].get("model", "unknown") for e in completed})
    usage = Counter()
    for e in completed:
        usage.update({k: v for k, v in e["output"].get("usage", {}).items() if type(v) is int})
    return {
        "arm": config.arm,
        "config": config.as_dict(),
        "status": run["status"],
        "run_id": run["run_id"],
        "revision": run["control_revision"],
        "quality": run.get("quality"),
        "failure": run.get("failure"),
        "actual_model_requests": run.get("actual_model_requests", 0),
        "journal_started": len(started),
        "completed_responses": len(completed),
        "operations": dict(Counter(e["input"]["context"]["operation"] for e in started)),
        "actual_model_ids": models,
        "tokens": dict(usage),
        "actual_reads": len(reads),
        "unique_reads": len(set(reads)),
        "read_documents": reads,
        "report_id": run.get("report_id"),
        "run_created_at": run["created_at"],
        "run_updated_at": run["updated_at"],
        "elapsed_boundary": "runner wall clock includes model waits, code, SQLite, correction and final evaluation; excludes preflight/export",
    }


def aggregate(paths):
    results = []
    manifest_path = paths["evidence"] / "준비.json"
    prepared = json.loads(manifest_path.read_text("utf-8")) if manifest_path.is_file() else None
    expected = identity(question=prepared["identity"]["question"]) if prepared else identity()
    if prepared and prepared["identity"] != expected:
        raise ValueError("comparison: aggregation prepared identity mismatch")
    for arm in ARMS:
        path = paths["evidence"] / arm / "결과.json"
        if path.is_file():
            result = json.loads(path.read_text("utf-8"))
            if (
                result.get("identity") != expected
                or result.get("arm") != arm
                or result.get("config") != ARMS[arm].as_dict()
            ):
                raise ValueError("comparison: aggregation identity/config mismatch")
            results.append(result)
    return {
        "protocol": PROTOCOL,
        "count": len(results),
        "status_counts": dict(Counter(r["status"] for r in results)),
        "actual_model_requests": sum(r.get("actual_model_requests", 0) for r in results),
        "results": results,
        "missing_arms": sorted(set(ARMS) - {r["arm"] for r in results}),
        "limitation": "조건당1회; 실패/차단을 포함하며 통계적 우열을 주장하지 않는다.",
    }


def verify_manifest(manifest, current, ledger):
    if manifest["identity"] != current or manifest["ledger"]["path"] != str(ledger):
        raise ValueError(
            "comparison: prepared source/config/corpus/ledger mismatch; use a new label"
        )
    if not preserved(manifest["ledger"], ledger_snapshot(ledger)):
        raise ValueError("comparison: prior ledger history changed")


def record_runner_failure(service, admitted, issue):
    """종료한 러너의 현재 작업만 종료 상태로 남긴다. 원 호출·원고·예약은 보존한다."""
    if admitted is None:
        return
    with service.repo.transaction() as (db, data):
        run = service.repo.fenced(data, admitted["run_id"], admitted["control_revision"])
        if run is None:
            return
        code = "experiment_timeout" if issue == "timeout" else "experiment_runner_error"
        message = (
            "비교 실험의 실행 제한시간을 넘겼습니다."
            if issue == "timeout"
            else "비교 실행기가 중단되었습니다."
        )
        service.fail(
            db,
            data,
            run["run_id"],
            run["control_revision"],
            "dispatcher",
            message,
            "execution.failed",
            safe_error={"code": code, "message": message},
        )


async def run_arm(
    arm, paths, ledger, expected, *, model_factory=None, timeout_seconds: float = 1800
):
    """명시 factory는 오프라인 테스트용. 실제 provider 생성은 이 함수 안뿐이다."""
    from 서버.실제연구제어 import RealResearchService

    config = ARMS[arm]
    data = within(paths["data"] / arm, ROOT / "03_실행데이터/제출_비교실험")
    evidence, cache = paths["evidence"] / arm, paths["cache"] / arm
    # mkdir(exist_ok=False): 완료·실패·중단된 어떤 기존 arm도 덮어쓰지 않는다.
    data.mkdir(parents=True, exist_ok=False)
    before = ledger_snapshot(ledger)
    save(
        evidence / "시작.json",
        {"identity": expected, "config": config.as_dict(), "ledger": before, "started_at": stamp()},
    )
    service = None
    admitted = None
    start, begun = perf_counter(), stamp()
    issue = None
    try:
        if model_factory is None:
            from 서버.실제모델 import OpenAIModel

            provider = OpenAIModel(ROOT / ".env", ledger)
        else:
            provider = model_factory()
        model = CappedModel(provider)
        service = RealResearchService(data, model, CORPUS, experiment_config=config)
        admitted = service.admit(expected["question"], f"comparison:{arm}")

        async def wait():
            while service.tasks:
                await asyncio.sleep(0.05)

        try:
            await asyncio.wait_for(wait(), timeout_seconds)
        except TimeoutError:
            issue = "timeout"
            await service.close()
            record_runner_failure(service, admitted, issue)
        elapsed = perf_counter() - start
        archive = service.repo.archive(admitted["run_id"])
        report = (
            service.report_bytes(archive["run"]["report_id"])[0]
            if archive["run"].get("report_id")
            else None
        )
        result = summarize(archive, config)
        save(cache / "실행아카이브.json", {**archive, "report": report})
        save(evidence / "사건.json", archive["events"])
        if archive["run"].get("draft"):
            (evidence / "원고.md").write_text(archive["run"]["draft"], encoding="utf-8")
        if report:
            (evidence / "보고서.md").write_text(report["markdown"], encoding="utf-8")
        result.update(
            elapsed_seconds=elapsed,
            forwarded=model.forwarded,
            actual_wrapper_attempts=model.actual_attempts,
        )
    except Exception as error:  # noqa: BLE001 -- 실패도 보존하되 제공자 원문/비밀은 기록하지 않는 최종 경계.
        # 원시 provider 오류/인증값은 저장하지 않는다. 실패를 결과에서 제거하지 않는다.
        issue = "runner_" + type(error).__name__
        result = {
            "arm": arm,
            "config": config.as_dict(),
            "status": "runner_failed",
            "actual_model_requests": getattr(locals().get("model"), "actual_attempts", 0),
        }
    finally:
        if service is not None:
            await service.close()
            if issue is not None:
                record_runner_failure(service, admitted, issue)
    after = ledger_snapshot(ledger)
    actual = result.get("actual_model_requests", 0)
    result["accounting_verified"] = (
        actual <= 55
        and after["attempt_count"] - before["attempt_count"] == actual
        and result.get("journal_started", actual) == actual
    )
    result.update(
        identity=expected,
        started_at=begun,
        ended_at=stamp(),
        runner_issue=issue,
        ledger_before=before,
        ledger_after=after,
        history_preserved=preserved(before, after),
        source_unchanged=identity(question=expected["question"]) == expected,
        budget_delta={
            key: after[key] - before[key]
            for key in (
                "attempt_count",
                "spent_nano_usd",
                "reserved_nano_usd",
                "input_tokens",
                "output_tokens",
            )
        },
    )
    save(evidence / "결과.json", result)
    return result


def adopt_baseline(bundle_path, paths, ledger, expected):
    """저장 archive와 실제 장부 receipt를 대조한다. DB 복원/제공자/재실행 없음."""
    bundle = json.loads(within(bundle_path, ROOT).read_text("utf-8"))
    run = bundle["run"]
    config = ARMS["baseline"]
    if (
        run.get("experiment_config") != config.as_dict()
        or run.get("experiment_metadata")
        != {k: expected[k] for k in ("source_hashes", "corpus_sha256", "model_id")}
        or run.get("question") != expected["question"]
        or run.get("source_mode", "local") != "local"
        or run.get("status") not in {"completed", "blocked", "failed", "cancelled", "interrupted"}
    ):
        raise ValueError("comparison: adoption metadata mismatch")
    result = summarize(bundle, config)
    starts = [e for e in bundle["events"] if e["type"] == "model.started"]
    receipts = [e["output"]["attempt_id"] for e in starts]
    snap = ledger_snapshot(ledger)
    if (
        not receipts
        or len(set(receipts)) != len(receipts)
        or len(receipts) > 55
        or len(receipts) != result["actual_model_requests"]
        or not set(receipts) <= set(snap["row_hashes"])
        or result["actual_model_ids"] != [MODEL_ID]
    ):
        raise ValueError("comparison: adoption receipt/model mismatch")
    report = bundle.get("report")
    if run["status"] == "completed" and (
        not report
        or report.get("report_id") != run["report_id"]
        or digest(report["markdown"].encode()) != report.get("sha256")
    ):
        raise ValueError("comparison: adoption report mismatch")
    dest = paths["evidence"] / "baseline"
    dest.mkdir(parents=True, exist_ok=False)
    result.update(
        identity=expected,
        adopted=True,
        adopted_at=stamp(),
        adoption_bundle_sha256=digest(Path(bundle_path).read_bytes()),
        budget_delta=None,
        elapsed_seconds=None,
        elapsed_boundary="adopted run creation/update timestamps; no runner duration or ledger delta inferred",
    )
    save(paths["cache"] / "baseline/실행아카이브.json", bundle)
    save(dest / "사건.json", bundle["events"])
    if run.get("draft"):
        (dest / "원고.md").write_text(run["draft"], encoding="utf-8")
    if report:
        (dest / "보고서.md").write_text(report["markdown"], encoding="utf-8")
    save(dest / "결과.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="읽기 전용 사전 검사; 제공자 미생성")
    modes.add_argument(
        "--prepare", action="store_true", help="조건/소스/장부 지문 고정; 제공자 미생성"
    )
    modes.add_argument("--execute", action="store_true", help="실제 제공자 실행 명시 승인 옵션")
    modes.add_argument(
        "--adopt-baseline",
        type=Path,
        help="동일 조건/소스/실제 receipt의 기준 실행 archive JSON 재사용",
    )
    parser.add_argument(
        "--ledger", type=Path, required=True, help="기존 공유 장부; 정책을 변경하지 않음"
    )
    parser.add_argument("--label", default="제출01")
    parser.add_argument("--arm", choices=[*ARMS, "all"], default="baseline")
    parser.add_argument("--question-key", choices=QUESTION_PRESETS, default="Q4")
    parser.add_argument(
        "--resume", action="store_true", help="저장된 결과는 실패도 다시 실행하지 않음"
    )
    args = parser.parse_args(argv)
    ledger = within(args.ledger, ROOT)
    paths = locations(args.label)
    current = identity(question=QUESTION_PRESETS[args.question_key])
    snapshot = ledger_snapshot(ledger)
    manifest_path = paths["evidence"] / "준비.json"
    if not (args.prepare or args.execute or args.adopt_baseline):
        print(
            json.dumps(
                {"identity": current, "ledger": snapshot, "provider_initialized": False},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.prepare:
        if manifest_path.exists():
            verify_manifest(json.loads(manifest_path.read_text("utf-8")), current, ledger)
        else:
            save(manifest_path, {"identity": current, "ledger": snapshot, "prepared_at": stamp()})
        print(str(manifest_path))
        return 0
    if not manifest_path.is_file():
        raise ValueError("comparison: --prepare required before execute/adopt")
    verify_manifest(json.loads(manifest_path.read_text("utf-8")), current, ledger)
    if args.adopt_baseline:
        adopt_baseline(args.adopt_baseline, paths, ledger, current)
    else:
        for arm in ARMS if args.arm == "all" else [args.arm]:
            target = paths["evidence"] / arm / "결과.json"
            if target.exists():
                prior = json.loads(target.read_text("utf-8"))
                if (
                    not args.resume
                    or prior["identity"] != current
                    or prior["config"] != ARMS[arm].as_dict()
                ):
                    raise ValueError("comparison: existing result; resume only identical metadata")
                continue
            result = asyncio.run(run_arm(arm, paths, ledger, current))
            if (
                not result["history_preserved"]
                or not result["source_unchanged"]
                or not result["accounting_verified"]
            ):
                raise ValueError("comparison: preservation/source drift; stop")
    total = aggregate(paths)
    save(paths["evidence"] / "집계.json", total)
    print(
        json.dumps(
            {
                "count": total["count"],
                "status_counts": total["status_counts"],
                "actual_model_requests": total["actual_model_requests"],
                "result": str(paths["evidence"] / "집계.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        print("비교 실행 거절: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
