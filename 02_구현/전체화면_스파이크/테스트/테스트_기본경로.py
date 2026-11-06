"""새 전체 화면 실행본의 기본 저장 경로 바인딩 검사."""

from pathlib import Path

import httpx

from 서버 import 앱 as app_module


async def test_기본저장은_이전스파이크가_아닌_새폴더에_연결된다(monkeypatch, request):
    observed = []

    class StorageBoundarySpy:
        def __init__(self, data_dir):
            observed.append(Path(data_dir))

        async def close(self):
            return None

    # 이전 사용자 DB를 만들거나 열지 않고 앱의 실제 기본 경로 선택만 관측한다.
    monkeypatch.delenv("SPIKE_DATA_DIR", raising=False)
    monkeypatch.setattr(app_module, "ExecutionService", StorageBoundarySpy)
    app = app_module.create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            assert (await client.get("/api/health")).status_code == 200
        expected = request.config.rootpath.parents[1] / "03_실행데이터" / "전체화면_스파이크"
        assert observed == [expected]
