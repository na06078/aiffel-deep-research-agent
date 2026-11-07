"""웹 연구 앱을 명시한 데이터·장부·화면 경로로 기동하는 검증용 기동기.

기본 기동기(`서버.연구앱`)는 인자가 포트 하나뿐이라 격리된 데이터 폴더나 장부 사본을 지정할 수 없다.
사용자 실행본(8766·8768)과 겹치지 않는 포트에서 새 코드를 시험할 때만 쓴다.
"""

import argparse
import sys
from pathlib import Path

import uvicorn

BASE = Path(__file__).resolve().parents[1]  # 02_구현/전체화면_스파이크
sys.path.insert(0, str(BASE))

from 서버.연구앱 import create_research_app

USER_PORTS = {8766, 8768}  # 사용자 실행본 포트


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--data-dir", type=Path, required=True, help="실행 기록을 둘 폴더(프로젝트 안)"
    )
    parser.add_argument("--ledger", type=Path, required=True, help="모델 호출 예산 장부 파일")
    parser.add_argument(
        "--static", type=Path, required=True, help="빌드한 화면 폴더(index.html 포함)"
    )
    args = parser.parse_args()
    if args.port in USER_PORTS or not 1024 <= args.port <= 65535:
        parser.error("사용자 실행본 포트이거나 허용 범위(1024~65535)를 벗어났습니다.")
    app = create_research_app(
        data_dir=args.data_dir, ledger_path=args.ledger, static_dir=args.static
    )
    print(f"READY port={args.port} data={args.data_dir.name} ledger={args.ledger.name}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
