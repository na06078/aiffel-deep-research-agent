"""표현 통합 전용: 실제 실행 그래프/SQLite + 명시적인 FakeModel. 운영 자료 미사용."""

import argparse
import asyncio
import ipaddress
import json
import os
import socket
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
sys.path.insert(0, str(SPIKE))
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


async def serve(data_dir, stop_file, port):
    import uvicorn
    from playwright.async_api import Browser

    from 서버.모델대역 import FakeModel
    from 서버.보고서변환 import render_report
    from 서버.실행제어 import ExecutionService
    from 서버.앱 import create_app

    # 실제 PDF 렌더러를 바꾸지 않고 그 Chromium context의 요청 이벤트만 관측합니다.
    pdf_requests = []
    renders = []
    original_context = Browser.new_context

    async def observed_context(browser, *args, **kwargs):
        context = await original_context(browser, *args, **kwargs)
        context.on(
            "request",
            lambda request: pdf_requests.append({"method": request.method, "url": request.url}),
        )
        return context

    Browser.new_context = observed_context

    async def observed_renderer(markdown, format, report):
        renders.append({"format": format, "report_id": report["report_id"]})
        return await render_report(markdown, format, report)

    for path in (data_dir, stop_file):
        if not path.resolve().is_relative_to(
            (ROOT / ".cache/보고서품질_검증/표현실사용").resolve()
        ):
            raise ValueError("시험 경로는 프로젝트 표현실사용 cache 내부만 허용합니다.")
    if stop_file.exists() or data_dir.exists():
        raise ValueError("기존 시험 자료를 초기화하지 않습니다. 새 실행 경로를 쓰세요.")
    original_connect = socket.socket.connect

    def loopback_connect(sock, address):
        host = address[0]
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise RuntimeError("표현시험 외부 소켓 차단")
        return original_connect(sock, address)

    socket.socket.connect = loopback_connect
    calls = {"plan": 0, "research": 0, "synthesize": 0, "external_model": 0, "web": 0}

    class PresentationModel(FakeModel):
        async def plan(self, payload):
            calls["plan"] += 1
            return await super().plan(payload)

        async def research(self, gate, payload):
            calls["research"] += 1
            return await super().research(gate, payload)

        async def synthesize(self, payload):
            calls["synthesize"] += 1
            body = await super().synthesize(payload)
            return (
                body
                + """

## 표현 검사용 합성 표

| 항목 | 값 | 출처 |
|---|---|---|
| 한글 보존 | 검증값 42 | «합성문서-1» |
| 파이프 | 가\\|나 | «합성문서-2» |

## 합성 도식 · 실제 연구 결과 아님

검증용 흐름 설명입니다. «합성문서-1»

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 160"><title>합성 검증 도식</title><desc>입력에서 보고서까지 · 시험 대역</desc><rect x="10" y="15" width="580" height="130" fill="#eef4ff" stroke="#234567"/><text x="35" y="85" font-size="24" fill="#123456">입력 → 합성 표 → 보고서</text></svg>
```

[검증용 출처 한글](https://example.invalid/presentation-source)

<script>window.__presentationUnsafe=true</script>

![차단된 외부 이미지](https://example.invalid/blocked.png)
"""
            )

    def factory(path):
        service = ExecutionService(path)
        service.model = PresentationModel()
        return service

    app = create_app(
        data_dir=data_dir,
        service_factory=factory,
        export_renderer=observed_renderer,
        static_dir=ROOT / ".cache/보고서품질_화면",
    )

    @app.get("/__test__/metrics")
    async def metrics():
        return {
            "provider": "test-double",
            "calls": calls,
            "pid": os.getpid(),
            "pdf_browser_requests": pdf_requests,
            "real_renderer_invocations": renders,
        }

    # create_app의 마지막 정적 '/' mount보다 시험 관측 GET을 앞에 둡니다.
    app.router.routes.insert(0, app.router.routes.pop())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    async def watch():
        while not stop_file.exists():
            await asyncio.sleep(0.1)
        server.should_exit = True

    watcher = asyncio.create_task(watch())
    print(
        json.dumps({"owned_pid": os.getpid(), "port": port, "provider": "test-double"}), flush=True
    )
    try:
        await server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        socket.socket.connect = original_connect
        Browser.new_context = original_context


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8794)
    args = parser.parse_args()
    asyncio.run(serve(args.data_dir, args.stop_file, args.port))
