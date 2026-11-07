"""앱 조립 경계만 확인한다. 모델·자료 클라이언트 전송은 하지 않는다."""

import hashlib
from types import SimpleNamespace

import pytest

from 서버 import 연구앱
from 서버.호출예산 import CallBudget


@pytest.fixture
def assembly(tmp_path, monkeypatch):
    root = tmp_path / "프로젝트"
    root.mkdir()
    monkeypatch.setattr(연구앱, "ROOT", root)
    captured = {}

    def fake_app(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(state=SimpleNamespace(service_factory=kwargs["service_factory"]))

    monkeypatch.setattr(연구앱, "create_app", fake_app)
    return root, captured


def test_격리데이터의실제모델에는_장부경로를반드시명시(assembly):
    root, _ = assembly
    with pytest.raises(ValueError, match="ledger_path"):
        연구앱.create_research_app(data_dir=root / "시험", web_client=object())


def test_자료루트가달라도_명시한공유장부와_자료클라이언트를연결(assembly, monkeypatch):
    root, captured = assembly
    ledger = root / "공유/호출예산.sqlite3"
    budget = CallBudget(ledger)
    budget.reset_attempt_counter(max_attempts=100, max_usd=2)
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    model, web = object(), object()
    seen = {}

    def fake_model(env_path, ledger_path):
        seen.update(env_path=env_path, ledger_path=ledger_path)
        return model

    def fake_service(path, provider, corpus_path, **kwargs):
        seen.update(path=path, provider=provider, corpus_path=corpus_path, **kwargs)
        return object()

    monkeypatch.setattr(연구앱, "OpenAIModel", fake_model)
    monkeypatch.setattr(연구앱, "RealResearchService", fake_service)
    app = 연구앱.create_research_app(
        data_dir=root / "시험", ledger_path=ledger, web_client=web, static_dir=root / "화면"
    )
    app.state.service_factory(root / "시험")
    assert seen["ledger_path"] == ledger
    assert seen["provider"] is model
    assert seen["web_client"] is web
    assert captured["static_dir"] == root / "화면"
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before


def test_장부없는실제기동을_새40회장부로대체하지않음(assembly):
    with pytest.raises(ValueError, match="예산 장부"):
        연구앱.create_research_app(web_client=object())


def test_주입모델은_인증파일이나장부를읽지않음(assembly, monkeypatch):
    root, _ = assembly
    model, web = object(), object()
    seen = {}

    def forbidden(*args, **kwargs):
        raise AssertionError("실제 모델 생성 금지")

    def fake_service(path, provider, corpus_path, **kwargs):
        seen.update(provider=provider, web_client=kwargs["web_client"])
        return object()

    monkeypatch.setattr(연구앱, "OpenAIModel", forbidden)
    monkeypatch.setattr(연구앱, "RealResearchService", fake_service)
    app = 연구앱.create_research_app(data_dir=root / "격리", model=model, web_client=web)
    app.state.service_factory(root / "격리")
    assert seen == {"provider": model, "web_client": web}
    assert not (root / ".env").exists()
    assert list(root.rglob("*.sqlite3")) == []


def test_비어있는기존장부도_새정책으로초기화하지않음(assembly):
    root, _ = assembly
    path = root / "03_실행데이터/실제연구/호출예산.sqlite3"
    path.parent.mkdir(parents=True)
    path.touch()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="예산 장부"):
        연구앱.create_research_app(web_client=object())
    assert path.read_bytes() == before == b""


@pytest.mark.parametrize("field", ["data_dir", "ledger_path", "static_dir"])
def test_외부경로를_조립시점에거절(assembly, field):
    root, captured = assembly
    kwargs = {field: root.parent / "프로젝트밖", "model": object(), "web_client": object()}
    with pytest.raises(ValueError, match="프로젝트"):
        연구앱.create_research_app(**kwargs)
    assert captured == {}
