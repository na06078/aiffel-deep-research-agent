"""승인된 loopback same-origin 경계와 설정 검사."""

import httpx
import pytest

from 서버.앱 import create_app
from 테스트.테스트_실행제어 import client_at, snapshot, until


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"Origin": "https://evil.example"}, "origin_forbidden"),
        ({"Origin": "http://127.0.0.1:9090"}, "origin_forbidden"),
        ({"Origin": "http://localhost"}, "origin_forbidden"),
        ({"Origin": "null"}, "origin_forbidden"),
        ({"Host": "evil.example"}, "host_forbidden"),
        ({"Host": "127.0.0.1.evil.example"}, "host_forbidden"),
    ],
)
async def test_교차출처는_양성대조후_접수전_차단된다(tmp_path, headers, code):
    async with client_at(tmp_path) as (client, _app):
        positive = await client.post(
            "/api/spike/commands",
            headers={"Origin": "http://127.0.0.1"},
            json={"text": "합성 양성 대조", "client_request_id": "positive"},
        )
        assert positive.status_code == 202
        # 정상 접수가 완료된 상태를 기준으로 부정 요청의 무부작용을 비교한다.
        before = await until(client, lambda s: len(s["waiting"]) == 4)
        bad = await client.post(
            "/api/spike/commands",
            headers=headers,
            json={"text": "차단할 지시", "client_request_id": "forbidden"},
        )
        assert bad.status_code == 403 and bad.json()["error"]["code"] == code
        assert await snapshot(client) == before


async def test_건강검사_정적화면_입력오류봉투와_프로젝트로컬설정(tmp_path, monkeypatch):
    static = tmp_path / "정적화면"
    static.mkdir()
    (static / "index.html").write_text("<h1>합성 시험 화면</h1>", encoding="utf-8")
    data = tmp_path / "주입저장소"
    monkeypatch.setenv("SPIKE_DATA_DIR", str(data))
    app = create_app(static_dir=static)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "mode": "spike",
            "provider": "test-double",
            "actual_model_requests": 0,
        }
        assert "합성 시험 화면" in (await client.get("/")).text
        assert app.state.service.repo.root == data.resolve()
        before = await snapshot(client)
        bad = await client.post("/api/spike/releases", json={"outcome": "bogus"})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
        bad = await client.get("/api/spike/events?after=oops")
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
        assert await snapshot(client) == before
