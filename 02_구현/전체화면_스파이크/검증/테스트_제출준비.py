"""제출 재현 준비의 데이터/장부 보존 계약. 실제 네트워크·모델은 사용하지 않는다."""

import importlib
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

I = Path(__file__).resolve().parents[1]
ROOT = I.parents[1]
sys.path.insert(0, str(ROOT))
materials = importlib.import_module("교안자료_준비")
runtime = importlib.import_module("실행준비")


@pytest.fixture
def prepared_root(tmp_path):
    target = tmp_path / "fresh-project"
    data = target / "01_참고자료/원본데이터"
    data.mkdir(parents=True)
    for name in ("corpus.json", "결과자료.json"):
        shutil.copyfile(ROOT / "01_참고자료/원본데이터" / name, data / name)
    return target


def test_자료변조는_덮어쓰지않고_거절한다(prepared_root):
    path = prepared_root / "01_참고자료/원본데이터/corpus.json"
    path.write_bytes(b"keep-my-existing-data")
    before = path.read_bytes()
    with pytest.raises(materials.PreparationError, match="existing_hash_mismatch"):
        materials.prepare_materials(prepared_root, download=True)
    assert path.read_bytes() == before


def test_검사모드는_장부를_만들지않는다(prepared_root):
    ledger = prepared_root / "03_실행데이터/실제연구/호출예산.sqlite3"
    with pytest.raises(runtime.PreparationError, match="not_initialized"):
        runtime.prepare_runtime(prepared_root)
    assert not ledger.exists()


def test_첫장부만_지정상한으로_생성한다(prepared_root):
    result = runtime.prepare_runtime(prepared_root, initialize=True, max_attempts=10000, max_usd=20)
    assert result.get("ledger_created") is True
    assert result["budget"]["attempt_count"] == 0
    assert result["budget"]["max_attempts"] == 10000
    assert result["budget"]["max_usd"] == 20
    assert result["actual_model_requests"] == 0


def test_기존장부와_미정산예약은_초기화하지않는다(prepared_root):
    from 서버.호출예산 import CallBudget

    path = prepared_root / "03_실행데이터/실제연구/호출예산.sqlite3"
    budget = CallBudget(path)
    budget.reset_attempt_counter(max_attempts=17, max_usd=3)
    attempt = budget.reserve(100, 10, {})
    before = path.read_bytes()
    result = runtime.prepare_runtime(prepared_root, initialize=True, max_attempts=10000, max_usd=20)
    assert result.get("ledger_created") is False
    assert result["budget"]["max_attempts"] == 17
    assert result["budget"]["attempt_count"] == 1
    assert result["budget"]["unknown_count"] == 1
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT id FROM attempts").fetchone()[0] == attempt


def test_실행자료만있고_장부가없으면_새예산으로_위장하지않는다(prepared_root):
    data = prepared_root / "03_실행데이터/실제연구"
    data.mkdir(parents=True)
    (data / "실행기록.sqlite3").write_bytes(b"existing-execution-record")
    with pytest.raises(runtime.PreparationError, match="missing_existing_ledger"):
        runtime.prepare_runtime(prepared_root, initialize=True)
    assert not (data / "호출예산.sqlite3").exists()


def test_정상기존자료_검사는_네트워크없이_34문서로_집계한다(prepared_root, monkeypatch):
    monkeypatch.setattr(materials, "download_archive", lambda: pytest.fail("unexpected_network"))
    result = materials.prepare_materials(prepared_root)
    assert result.get("documents") == 34
    assert result["links"] == 428
    assert result["downloaded"] is False


def test_잘못된다운로드는_새자료를_생성하지않는다(tmp_path, monkeypatch):
    target = tmp_path / "invalid-download"
    monkeypatch.setattr(materials, "download_archive", lambda: b"invalid-archive")
    with pytest.raises(materials.PreparationError, match="archive_hash_mismatch"):
        materials.prepare_materials(target, download=True)
    assert not target.exists()


def test_고정ZIP의_바이트를_검증해_새자료를_준비한다(tmp_path, monkeypatch):
    archive = (ROOT / "01_참고자료/원본데이터/교안_원본자료.zip").read_bytes()
    monkeypatch.setattr(materials, "download_archive", lambda: archive)
    target = tmp_path / "valid-download"
    result = materials.prepare_materials(target, download=True)
    assert result.get("documents") == 34
    assert result["downloaded"] is True
    for name in ("corpus.json", "결과자료.json"):
        assert (target / "01_참고자료/원본데이터" / name).read_bytes() == (
            ROOT / "01_참고자료/원본데이터" / name
        ).read_bytes()
