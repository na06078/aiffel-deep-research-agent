"""실제 연구 앱의 조립 진입점."""

import argparse
import os
from pathlib import Path

from 서버.실제모델 import OpenAIModel
from 서버.실제연구제어 import RealResearchService
from 서버.앱 import create_app

ROOT = Path(__file__).resolve().parents[3]


def create_research_app(*, data_dir=None, model=None):
    # LangGraph의 관측 자료를 외부 추적 서비스로 자동 전송하지 않는다.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    target = Path(data_dir or ROOT / "03_실행데이터/실제연구").resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError("실제 연구 저장소는 이 프로젝트 안에 있어야 합니다.")

    def factory(path):
        provider = (
            model if model is not None else OpenAIModel(ROOT / ".env", target / "호출예산.sqlite3")
        )
        return RealResearchService(path, provider, ROOT / "01_참고자료/원본데이터/corpus.json")

    return create_app(data_dir=target, service_factory=factory)


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="고정 교안 자료를 사용하는 실제 연구 마을")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("포트는 1~65535 사이여야 합니다.")
    uvicorn.run(create_research_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
