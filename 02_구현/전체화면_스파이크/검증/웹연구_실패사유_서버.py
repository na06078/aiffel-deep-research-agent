"""모델이 즉시 실패하는 대역을 주입한 격리 서버 기동기(실패 사유 화면 검사 전용).

서버가 만드는 진짜 실패 경로(`fail()`)를 화면까지 확인하되 외부 모델 호출·검색·유료 비용은 0이다.
실제 연구에 쓰는 기동기(`웹연구_격리서버.py`)와 달리 모델 대역을 쓰므로 연구 결과를 만들지 않는다.
"""

import argparse
import sys
from pathlib import Path

import uvicorn

BASE = Path(__file__).resolve().parents[1]  # 02_구현/전체화면_스파이크
sys.path.insert(0, str(BASE))

from 서버.연구앱 import create_research_app

USER_PORTS = {8766, 8768}  # 사용자 실행본 포트


class AlwaysFailModel:
    """호출되는 즉시 실패한다. 앱의 오류 경로가 사유를 run에 남기는지 보려는 용도다."""

    async def complete(self, system, user, *, context, on_attempt, **kwargs):
        raise RuntimeError("synthetic provider failure")

    def usage(self):
        return {
            "attempt_count": 0,
            "max_attempts": 100,
            "remaining_attempts": 100,
            "max_usd": 2.0,
            "spent_usd": 0.0,
            "reserved_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "unknown_count": 0,
        }

    async def aclose(self):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True, help="새 폴더(프로젝트 안)")
    parser.add_argument("--static", type=Path, required=True, help="빌드한 화면 폴더")
    args = parser.parse_args()
    if args.port in USER_PORTS or not 1024 <= args.port <= 65535:
        parser.error("사용자 실행본 포트이거나 허용 범위(1024~65535)를 벗어났습니다.")
    app = create_research_app(
        data_dir=args.data_dir, model=AlwaysFailModel(), static_dir=args.static
    )
    print(f"READY port={args.port} data={args.data_dir.name} model=AlwaysFailModel", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
