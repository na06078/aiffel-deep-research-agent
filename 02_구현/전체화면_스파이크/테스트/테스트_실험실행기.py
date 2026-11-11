"""실행기 안전성: 모든 제공자 명시 대역, SQLite는 작업별 격리 경로만."""

import asyncio
import json
import sqlite3
from contextlib import closing

import pytest

from 검증 import 비교실험_실행 as runner
from 서버.교안연구 import ResearchContractError
from 서버.실제모델 import ModelCallError
from 서버.실제연구제어 import RealResearchService
from 서버.실험설정 import ARMS
from 서버.호출예산 import CallBudget
from 테스트.테스트_실제연구제어 import CORPUS, ScriptedModel, no_external_requests  # noqa: F401
from 테스트.테스트_실험설정 import ExperimentModel


async def test_UI설정서비스도_55회를넘으면_모델전달전_차단(tmp_path):
    model = ScriptedModel()
    service = RealResearchService(tmp_path, model, CORPUS, experiment_config=ARMS["baseline"])
    with service.repo.transaction() as (db, data):
        run, _ = service.start(db, data, "질문", "cap")
        run["actual_model_requests"] = 55
        service.repo.save_revision(db, run)
    try:
        with pytest.raises(ResearchContractError, match="experiment: request limit"):
            await service.complete(
                {"run_id": run["run_id"], "revision": 1}, "planner", "plan", ("JSON", "JSON")
            )
        assert model.calls == []
        with service.repo.read_connection() as db:
            assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
    finally:
        await service.close()


async def test_동시forwarding도_55회이후는_제공자호출없음():
    class Fake:
        calls = 0

        async def complete(self, system, user, *, on_attempt, **kwargs):
            self.calls += 1
            on_attempt(str(self.calls))
            await asyncio.sleep(0)
            return {"content": "{}"}

    provider = Fake()
    model = runner.CappedModel(provider)
    results = await asyncio.gather(
        *(model.complete("s", "u", on_attempt=lambda _: None) for _ in range(60)),
        return_exceptions=True,
    )
    assert provider.calls == model.forwarded == model.actual_attempts == 55
    failures = [r for r in results if isinstance(r, ModelCallError)]
    assert len(failures) == 5 and all(e.code == "experiment_request_limit" for e in failures)


@pytest.fixture
def isolated_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "IMPLEMENTATION", tmp_path / "구현")
    ledger = tmp_path / "공유/호출예산.sqlite3"
    budget = CallBudget(ledger)
    for _ in range(6):
        budget.reserve(1, 1, {})
    with closing(sqlite3.connect(ledger)) as db:
        db.execute(
            "UPDATE budget_policy SET max_attempts=10000,max_nano_usd=20000000000 WHERE id=1"
        )
        db.commit()
    return ledger, budget


def test_help_check_prepare는_제공자미생성과_unknown6보존(isolated_runner, monkeypatch, capsys):
    from 서버 import 실제모델

    def deny(*args, **kwargs):
        raise AssertionError("provider_constructed")

    monkeypatch.setattr(실제모델, "OpenAIModel", deny)
    ledger, _ = isolated_runner
    before = ledger.read_bytes()
    with pytest.raises(SystemExit) as ex:
        runner.main(["--help"])
    assert ex.value.code == 0
    assert runner.main(["--check", "--ledger", str(ledger)]) == 0
    assert runner.main(["--prepare", "--ledger", str(ledger), "--label", "오프라인"]) == 0
    assert ledger.read_bytes() == before
    manifest = json.loads(
        (runner.locations("오프라인")["evidence"] / "준비.json").read_text("utf-8")
    )
    assert len(manifest["ledger"]["unknown_ids"]) == 6
    assert not runner.locations("오프라인")["data"].exists()
    assert "provider_initialized" in capsys.readouterr().out


async def test_명시대역으로_실행결과_실패보존_resume_기준재사용(isolated_runner, monkeypatch):
    ledger, budget = isolated_runner

    class LedgerFake(ExperimentModel):
        async def complete(self, *args, on_attempt, **kwargs):
            attempt = budget.reserve(100000, 4096, kwargs["context"])
            response = await super().complete(
                *args, on_attempt=lambda _: on_attempt(attempt), **kwargs
            )
            budget.settle(attempt, 11, 13)
            return {**response, "attempt_id": attempt, "model": runner.MODEL_ID}

    before = runner.ledger_snapshot(ledger)
    paths, expected = runner.locations("대역"), runner.identity()
    result = await runner.run_arm(
        "baseline", paths, ledger, expected, model_factory=lambda: LedgerFake(ARMS["baseline"])
    )
    assert result["status"] == "completed", result
    assert result["history_preserved"] and result["source_unchanged"]
    assert result["actual_model_ids"] == ["gpt-5.6-luna"]
    assert result["budget_delta"]["attempt_count"] == result["actual_model_requests"]
    assert result["tokens"]["input_tokens"] == 11 * result["actual_model_requests"]
    assert runner.preserved(before, runner.ledger_snapshot(ledger))
    assert len(runner.ledger_snapshot(ledger)["unknown_ids"]) == 6
    # 동일 조건의 원 archive를 새 비교 묶음의 baseline으로 채택, 새 요청/장부변경 없음.
    bundle = paths["cache"] / "baseline/실행아카이브.json"
    stable = ledger.read_bytes()
    adopted = runner.adopt_baseline(bundle, runner.locations("재사용"), ledger, expected)
    assert adopted["adopted"] and ledger.read_bytes() == stable
    for mutation in ("config", "source", "receipt", "model", "report"):
        invalid = json.loads(bundle.read_text("utf-8"))
        if mutation == "config":
            invalid["run"]["experiment_config"]["reads_per_section"] = 1
        elif mutation == "source":
            invalid["run"]["experiment_metadata"]["corpus_sha256"] = "0" * 64
        elif mutation == "receipt":
            next(e for e in invalid["events"] if e["type"] == "model.started")["output"][
                "attempt_id"
            ] = "not-in-ledger"
        elif mutation == "model":
            next(e for e in invalid["events"] if e["type"] == "model.completed")["output"][
                "model"
            ] = "other-model"
        else:
            invalid["report"]["markdown"] += " 변조"
        invalid_path = paths["cache"] / (mutation + ".json")
        runner.save(invalid_path, invalid)
        rejected_paths = runner.locations("거절_" + mutation)
        with pytest.raises(ValueError, match="adoption .* mismatch"):
            runner.adopt_baseline(invalid_path, rejected_paths, ledger, expected)
        assert not (rejected_paths["evidence"] / "baseline").exists()
        assert ledger.read_bytes() == stable

    # 실패 조건도 저장하고 같은 data root의 재실행은 거절한다.
    class Failed(LedgerFake):
        async def complete(self, *args, **kwargs):
            raise ModelCallError("fixture_failure", "명시 대역 실패")

    failed = await runner.run_arm(
        "depth1", paths, ledger, expected, model_factory=lambda: Failed(ARMS["depth1"])
    )
    assert failed["status"] == "failed"
    with pytest.raises(FileExistsError):
        await runner.run_arm(
            "depth1", paths, ledger, expected, model_factory=lambda: Failed(ARMS["depth1"])
        )
    assert runner.aggregate(paths)["status_counts"] == {"completed": 1, "failed": 1}
    runner.save(paths["evidence"] / "준비.json", {"identity": expected, "ledger": before})

    def forbidden(*args, **kwargs):
        raise AssertionError("resume_must_not_start_provider")

    monkeypatch.setattr(runner, "run_arm", forbidden)
    assert (
        runner.main(
            ["--execute", "--ledger", str(ledger), "--label", "대역", "--arm", "depth1", "--resume"]
        )
        == 0
    )
    with pytest.raises(ValueError, match="existing result"):
        runner.main(["--execute", "--ledger", str(ledger), "--label", "대역", "--arm", "depth1"])


def test_실행승인없음과_잘못된정책_경로를_안전하게거절(isolated_runner):
    ledger, _ = isolated_runner
    with pytest.raises(ValueError, match="prepare required"):
        runner.main(["--execute", "--ledger", str(ledger), "--label", "준비전"])
    with pytest.raises(ValueError, match="invalid label"):
        runner.locations("../운영")
    with closing(sqlite3.connect(ledger)) as db:
        db.execute("UPDATE budget_policy SET max_attempts=9999 WHERE id=1")
        db.commit()
    with pytest.raises(ValueError, match="policy must remain"):
        runner.ledger_snapshot(ledger)


async def test_시간초과는_작업종료와_DB결과집계를_일치시키고_resume는_재전송하지않는다(
    isolated_runner, monkeypatch
):
    ledger, budget = isolated_runner
    before = runner.ledger_snapshot(ledger)

    class SlowModel:
        started = 0
        closed = False

        async def complete(self, system, user, *, on_attempt, **kwargs):
            self.started += 1
            attempt = budget.reserve(100000, 4096, kwargs["context"])
            on_attempt(attempt)
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    model = SlowModel()
    paths, expected = runner.locations("시간초과"), runner.identity()
    result = await runner.run_arm(
        "baseline", paths, ledger, expected, model_factory=lambda: model, timeout_seconds=0.1
    )
    assert model.started == 1 and model.closed
    assert result["runner_issue"] == "timeout"
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "experiment_timeout"
    assert result["actual_model_requests"] == 1
    assert result["history_preserved"] and result["accounting_verified"]
    with closing(sqlite3.connect(paths["data"] / "baseline/실행기록.sqlite3")) as db:
        data = json.loads(db.execute("SELECT body FROM state").fetchone()[0])
        assert data["runs"][0]["status"] == "failed"
        assert data["queue_paused"] is True
    assert runner.aggregate(paths)["status_counts"] == {"failed": 1}
    runner.save(paths["evidence"] / "준비.json", {"identity": expected, "ledger": before})
    stable = ledger.read_bytes()

    async def forbidden(*args, **kwargs):
        raise AssertionError("timeout_resume_must_not_resend")

    monkeypatch.setattr(runner, "run_arm", forbidden)
    assert (
        runner.main(
            [
                "--execute",
                "--ledger",
                str(ledger),
                "--label",
                "시간초과",
                "--arm",
                "baseline",
                "--resume",
            ]
        )
        == 0
    )
    assert ledger.read_bytes() == stable
