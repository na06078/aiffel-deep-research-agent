"""프로젝트 내부 합성 자료와 명시적 렌더 실패만 사용하는 HTTP 시험 작업자."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
sys.path.insert(0, str(SPIKE))
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"


async def serve(data_dir, stop_file, port, fail_first_pdf):
    import uvicorn

    from 서버.모델대역 import FakeModel
    from 서버.보고서변환 import render_report
    from 서버.실행제어 import ExecutionService
    from 서버.앱 import create_app

    for path in (data_dir, stop_file):
        if not path.resolve().is_relative_to(ROOT / ".cache"):
            raise ValueError("변환 시험 자료/종료 표식은 프로젝트 .cache 내부만 사용합니다.")

    class LongDocumentModel(FakeModel):
        async def synthesize(self, payload):
            body = await super().synthesize(payload)
            if "긴 본문" not in payload["instruction"] or "불합격 합성 초안" in body:
                return body
            body += "\n\n## 표와 긴 본문 검증\n\n| 항목 | 값 |\n|---|---|\n| 한글 | 보존 |\n"
            for index in range(32):
                body += (
                    f"\n\n### 합성 장문 {index + 1}\n\n"
                    + (
                        "이 문장은 PDF 페이지 경계와 한글 줄바꿈을 확인하는 합성 자료입니다. "
                        "새로운 역사적 사실이나 실제 모델의 연구 결과가 아닙니다. "
                    )
                    * 8
                    + " «합성문서-1»"
                )
            body += (
                "\n\n## 합성 출처\n\n"
                "[검증용 출처](https://example.invalid/source) · CC BY-SA 4.0\n\n"
                "<script>window.__unsafeExport=true</script>\n\n"
                "![외부 이미지 차단 확인](https://example.invalid/forbidden-image.png)"
            )
            return body

    def factory(path):
        service = ExecutionService(path)
        service.model = LongDocumentModel()
        return service

    failed = False

    async def controlled_renderer(markdown, format, report):
        nonlocal failed
        await asyncio.sleep(0.8)
        if fail_first_pdf and format == "pdf" and not failed:
            failed = True
            raise RuntimeError("합성 검사의 첫 PDF 실패")
        return await render_report(markdown, format, report)

    app = create_app(
        data_dir=data_dir,
        service_factory=factory,
        export_renderer=controlled_renderer,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    async def watch_owned_marker():
        while not stop_file.exists():
            await asyncio.sleep(0.1)
        server.should_exit = True

    print(f"EXPORT_TEST_WORKER pid={os.getpid()} port={port}", flush=True)
    watcher = asyncio.create_task(watch_owned_marker())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--fail-first-pdf", action="store_true")
    args = parser.parse_args()
    asyncio.run(serve(args.data_dir, args.stop_file, args.port, args.fail_first_pdf))
