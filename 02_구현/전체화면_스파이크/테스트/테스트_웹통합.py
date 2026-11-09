"""정상 앱 조립→HTTP→6노드→실제 source client/store/저널의 무전송 통합.

모델 응답과 전송 backend만 명시 대역이다. 사용자 데이터/장부와 외부망은 사용하지 않는다.
"""

import json
import re
import socket
import time

import httpcore
import httpx
import pytest
from fastapi.testclient import TestClient

from 서버.연구앱 import create_research_app
from 서버.웹자료 import FreeWebClient
from 서버.웹자료저장소 import WebSourceStore
from 테스트.테스트_웹연구 import WebModel
from 테스트.테스트_웹자료 import WireBackend, public_dns, response


class IntegrationModel(WebModel):
    def usage(self):
        return {
            "attempt_count": len(self.calls),
            "max_attempts": 100,
            "remaining_attempts": 100 - len(self.calls),
            "max_usd": 2.0,
            "spent_usd": 0.0,
            "reserved_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "unknown_count": 0,
        }

    async def complete(self, system, user, *, context, on_attempt, **kwargs):
        if context["operation"] != "draft_section":
            return await super().complete(
                system, user, context=context, on_attempt=on_attempt, **kwargs
            )
        # 기존 단위 대역의 source-* 가정 대신 실제 저장소가 발급한 opaque ID를 읽는다.
        ids = re.findall(r"\[source_id: ([0-9a-f]{32})\]", user)
        assert len(ids) == 3
        # 실제 모델의 관측 동작: source_id 인용 지시가 없으면 메모 제목을 인용 키로 쓴다.
        key = ids[0] if "source_id" in system else "메모 제목"
        self.calls.append((system, user, context))
        attempt = f"integration-{len(self.calls)}"
        on_attempt(attempt)
        body = (("실제 공개 웹이 아닌 명시적 통합 대역의 문장입니다. " * 4) + f"«{key}».\n") * 22
        return {
            "content": json.dumps(
                {
                    "본문": body,
                    "충분": True,
                    "부족": "",
                    "근거": [
                        {
                            "주장": "실제 공개 웹이 아닌 명시적 통합 대역의 문장입니다.",
                            "출처": ids[0],
                            "인용문": self.excerpts[ids[0]],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "attempt_id": attempt,
            "request_id": "explicit-fake-provider",
            "model": "explicit-integration-fake",
            "usage": {},
        }


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    async def async_denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", async_denied)


def source_boundary():
    search_calls = []

    def handler(request):
        search_calls.append(str(request.url))
        html = (
            "<html>"
            + "".join(
                f'<div class="relsrch"><div class="Title"><h3>같은 제목</h3>'
                f'<a href="https://public.example/{i}">원문</a></div><div class="Text">대역</div></div>'
                for i in range(1, 4)
            )
            + "</html>"
        )
        return httpx.Response(200, text=html)

    def transport(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    wire = WireBackend(response(), response(), response())
    source = FreeWebClient(
        resolver=public_dns, network_backend=wire, search_http_client_factory=transport
    )
    return source, search_calls, wire


def test_정상조립과실제자료저장소_HTTP연구_재시작_무재전송(tmp_path):
    source, searches, wire = source_boundary()
    model = IntegrationModel()
    app = create_research_app(data_dir=tmp_path, model=model, web_client=source)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = client.get("/api/spike/snapshot").json()
        assert first["selected_source_mode"] == "local"
        assert first["source_modes"] == ["local", "web"]
        chosen = client.post(
            "/api/spike/commands", json={"text": "/웹", "client_request_id": "mode"}
        )
        assert chosen.status_code == 202 and chosen.json()["kind"] == "mode_change"
        assert model.calls == searches == wire.connects == []
        assert client.get("/api/spike/snapshot").json()["runs"] == []
        admitted = client.post(
            "/api/spike/commands", json={"text": "공개 자료 범위", "client_request_id": "research"}
        )
        assert admitted.status_code == 202
        run_id = admitted.json()["run_id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = client.get("/api/spike/snapshot").json()
            run = next(r for r in state["runs"] if r["run_id"] == run_id)
            if run["status"] != "running":
                break
            time.sleep(0.01)
        assert run["status"] == "completed", run
        assert len(model.calls) == 10 and len(searches) == 1 and len(wire.connects) == 3
        service = app.state.service
        assert isinstance(service.web_store, WebSourceStore)
        claims = service.web_store.read_claims(run_id, 1)
        assert len(claims) == 3
        report = client.get(f"/api/spike/reports/{run['report_id']}").json()
        assert report["source_mode"] == "web" and len(report["sources"]) == 3
        assert "https://public.example/" in report["markdown"]
        assert "wikipedia.org" not in report["markdown"]
        sources = service.web_store.sources(run_id, 1)
        assert len(sources) == 3 and len({s["title"] for s in sources}) == 1
        assert all(s["published_at"] is None for s in sources)
        seq = state["high_watermark_seq"]
        duplicate = client.post(
            "/api/spike/commands", json={"text": "공개 자료 범위", "client_request_id": "research"}
        )
        assert duplicate.json()["run_id"] == run_id
        assert client.get("/api/spike/snapshot").json()["high_watermark_seq"] == seq

    restarted_source, restarted_searches, restarted_wire = source_boundary()
    restarted_model = IntegrationModel()
    restarted_app = create_research_app(
        data_dir=tmp_path, model=restarted_model, web_client=restarted_source
    )
    with TestClient(restarted_app, base_url="http://127.0.0.1") as client:
        state = client.get("/api/spike/snapshot").json()
        assert state["selected_source_mode"] == "web" and len(state["runs"]) == 1
        assert len(restarted_app.state.service.web_store.read_claims(run_id, 1)) == 3
        assert restarted_model.calls == restarted_searches == restarted_wire.connects == []
        rejected = client.post(
            "/api/spike/commands",
            json={"text": "전송 없음", "source_mode": None, "client_request_id": "null"},
        )
        assert rejected.status_code == 422
