"""실제 HTTP 재시작 검사에만 쓰는 앱 소유 작업자. 공개 종료 API는 만들지 않는다."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPIKE))
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import uvicorn

from 서버.앱 import create_app


async def serve(data_dir: Path, stop_file: Path, port: int) -> None:
    root = SPIKE.parents[1]
    if not data_dir.resolve().is_relative_to(root) or not stop_file.resolve().is_relative_to(root):
        raise ValueError("시험 데이터와 종료 표식은 이 프로젝트 안에 있어야 합니다.")
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(data_dir=data_dir), host="127.0.0.1", port=port, log_level="warning"
        )
    )

    async def watch_owned_marker() -> None:
        while not stop_file.exists():
            await asyncio.sleep(0.1)
        server.should_exit = True

    print(f"SPIKE_TEST_WORKER pid={os.getpid()} port={port}", flush=True)
    watcher = asyncio.create_task(watch_owned_marker())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    asyncio.run(serve(args.data_dir, args.stop_file, args.port))
