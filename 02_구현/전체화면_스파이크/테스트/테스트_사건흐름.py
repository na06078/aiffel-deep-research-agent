"""실제 ASGI SSE 프레임·불변 상세·일관된 커서 검사."""

import asyncio
import json

from 테스트.테스트_실행제어 import client_at, command, snapshot, until


async def sse_frames(app, after, last_event_id, count):
    """무한 SSE를 버퍼링하지 않고 ASGI send/receive에서 연결 종료한다."""
    sent = []
    disconnect = asyncio.Event()
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        body = b"".join(m.get("body", b"") for m in sent)
        if body.count(b"data: ") >= count:
            disconnect.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/spike/events",
        "raw_path": b"/api/spike/events",
        "query_string": f"after={after}".encode(),
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1"), (b"last-event-id", str(last_event_id).encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(disconnect.wait(), timeout=2)
        await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200
    assert any(k == b"content-type" and b"text/event-stream" in v for k, v in start["headers"])
    body = b"".join(m.get("body", b"") for m in sent).decode()
    events = []
    for block in body.split("\n\n"):
        if "data: " in block:
            lines = block.splitlines()
            cursor = int(next(line[4:] for line in lines if line.startswith("id: ")))
            event = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
            assert event["seq"] == cursor
            assert not any(line.startswith("event:") for line in lines)
            events.append(event)
    return events


async def test_선택사건은_불변이고_최근100과_SSE_높은커서로_복원한다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        await command(client, "합성 사건 원본", "a")
        state = await until(client, lambda s: len(s["waiting"]) == 4)
        selected = next(e for e in state["events"] if e["type"] == "model.completed")
        detail_url = f"/api/spike/events/{selected['event_id']}"
        response = await client.get(detail_url)
        assert response.status_code == 200
        detail = response.json()
        assert detail["input"]["instruction"] == "합성 사건 원본"
        assert len(detail["output"]) == 4 and detail["provider"] == "test-double"
        for i in range(105):
            result = await command(client, f"/큐 합성 후속 {i}", f"q{i}")
            assert result.status_code == 202
        current = await snapshot(client)
        assert len(current["events"]) == 100 and current["events_has_more"]
        assert current["high_watermark_seq"] == current["events"][-1]["seq"]
        assert (await client.get(detail_url)).json() == detail
        before = current["mock_requests"]
        watermark = current["high_watermark_seq"]
        messages = await sse_frames(app, watermark - 4, watermark - 2, 2)
        assert [e["seq"] for e in messages] == [watermark - 1, watermark]
        messages = await sse_frames(app, watermark - 1, watermark - 4, 1)
        assert [e["seq"] for e in messages] == [watermark]
        assert (await snapshot(client))["mock_requests"] == before
        for url in [
            "/api/spike/events/missing",
            "/api/spike/reports/missing",
            "/api/spike/reports/missing/download",
        ]:
            bad = await client.get(url)
            assert bad.status_code == 404 and bad.json()["error"]["code"] == "not_found"
        assert await snapshot(client) == current


async def test_조사_모델입력에_격리된_실제_합성문서가_저장된다(tmp_path):
    async with client_at(tmp_path) as (client, _app):
        await command(client, "합성 입력 관찰", "a")
        state = await until(client, lambda s: len(s["waiting"]) == 4)
        events = [e for e in state["events"] if e["actor"] == "researcher-1"]
        detail = (await client.get(f"/api/spike/events/{events[0]['event_id']}")).json()
        assert detail["input"].get("document_id") == "합성문서-1"
        assert "이것은 합성문서-1의 시험용 원문이다." in detail["input"]["source"]
        assert detail["input"]["instruction"] == "합성 입력 관찰"
