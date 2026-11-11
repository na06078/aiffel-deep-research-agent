"""실제 연구 앱의 조립 진입점."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from 서버.실제모델 import OpenAIModel
from 서버.실제연구제어 import RealResearchService
from 서버.앱 import create_app

ROOT = Path(__file__).resolve().parents[3]


def _validate_existing_ledger(path):
    """기존 장부를 읽기 전용으로 검사해 누락을 새 예산으로 위장하지 않는다."""
    if not path.is_file():
        raise ValueError("기존 예산 장부를 찾을 수 없습니다. 장부 경로와 복구 상태를 확인하십시오.")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            policy = db.execute(
                "SELECT max_attempts,max_nano_usd,reset_after_rowid FROM budget_policy WHERE id=1"
            ).fetchone()
            maximum = db.execute("SELECT coalesce(max(rowid),0) FROM attempts").fetchone()[0]
    except sqlite3.Error:
        raise ValueError("기존 예산 장부의 정책을 확인할 수 없습니다.") from None
    if (
        policy is None
        or any(type(value) is not int for value in policy)
        or policy[0] <= 0
        or policy[1] <= 0
        or not 0 <= policy[2] <= maximum
    ):
        raise ValueError("기존 예산 장부의 정책이 올바르지 않습니다.")


def create_research_app(
    *,
    data_dir=None,
    model=None,
    ledger_path=None,
    web_client=None,
    static_dir=None,
    experiment_config=None,
):
    from 서버.실험설정 import ExperimentConfig

    if experiment_config is not None and type(experiment_config) is not ExperimentConfig:
        raise ValueError("experiment: validated ExperimentConfig required")
    # LangGraph의 관측 자료를 외부 추적 서비스로 자동 전송하지 않는다.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    target = Path(data_dir or ROOT / "03_실행데이터/실제연구").resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError("실제 연구 저장소는 이 프로젝트 안에 있어야 합니다.")
    if model is None and data_dir is not None and ledger_path is None:
        raise ValueError("격리된 실제 모델 실행에는 공유 ledger_path를 명시하십시오.")
    ledger = Path(ledger_path or ROOT / "03_실행데이터/실제연구/호출예산.sqlite3").resolve()
    if not ledger.is_relative_to(ROOT):
        raise ValueError("예산 장부는 이 프로젝트 안에 있어야 합니다.")
    if model is None:
        _validate_existing_ledger(ledger)
    static = Path(static_dir).resolve() if static_dir is not None else None
    if static is not None and not static.is_relative_to(ROOT):
        raise ValueError("화면 파일은 이 프로젝트 안에 있어야 합니다.")

    def factory(path):
        provider = model if model is not None else OpenAIModel(ROOT / ".env", ledger)
        client = web_client
        if client is None and experiment_config is None:
            from 서버.웹자료 import FreeWebClient

            client = FreeWebClient()
        return RealResearchService(
            path,
            provider,
            ROOT / "01_참고자료/원본데이터/corpus.json",
            web_client=client,
            experiment_config=experiment_config,
        )

    return create_app(data_dir=target, static_dir=static, service_factory=factory)


def main():
    import uvicorn

    parser = argparse.ArgumentParser(
        description="데이터셋과 무료 웹 검색을 사용하는 실제 연구 마을"
    )
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1~65535 사이여야 합니다.")
    uvicorn.run(create_research_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
