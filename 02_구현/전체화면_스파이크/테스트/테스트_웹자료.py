"""네트워크 백엔드만 대역으로 교체하며 실제 httpcore/TLS 경로를 실행한다."""

import asyncio
import hashlib
import ssl

import httpcore
import pytest

from 서버.웹계약 import WebSourceError
from 서버.웹자료 import FreeWebClient


@pytest.fixture(autouse=True)
def forbid_default_network(monkeypatch):
    import socket

    import httpx

    def denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    async def async_denied(*args, **kwargs):
        raise AssertionError("real_network_forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", async_denied)


ARTICLE = (
    "<html><head><title>테스트 기사</title></head><body><article><h1>기사</h1><p>"
    + "공개된 연구 결과를 소개합니다. 자료의 출처와 결과를 자세히 설명합니다. " * 30
    + "</p></article></body></html>"
).encode()


def response(body=ARTICLE, status=200, headers=None):
    fields = {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(body)),
        "Connection": "close",
    } | (headers or {})
    return (
        f"HTTP/1.1 {status} OK\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
    ).encode() + body


class WireStream(httpcore.AsyncNetworkStream):
    def __init__(self, backend, data):
        self.backend, self.data = backend, data

    async def read(self, max_bytes, timeout=None):
        data, self.data = self.data[:max_bytes], self.data[max_bytes:]
        return data

    async def write(self, buffer, timeout=None):
        self.backend.writes.append(bytes(buffer))

    async def aclose(self):
        self.backend.closes += 1

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.backend.tls.append((ssl_context, server_hostname))
        return self

    def get_extra_info(self, info):
        return None


class WireBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, *responses):
        self.responses = list(responses or [response()])
        self.connects, self.tls, self.writes, self.closes = [], [], [], 0

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connects.append((host, port))
        return WireStream(self, self.responses.pop(0))


async def public_dns(host, port):
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url,addresses,code",
    [
        ("http://127.0.0.1/", ["93.184.216.34"], "unsafe_target"),
        ("http://[::1]/", ["93.184.216.34"], "unsafe_target"),
        ("http://[fec0::1]/", ["93.184.216.34"], "unsafe_target"),
        ("http://[::ffff:127.0.0.1]/", ["93.184.216.34"], "unsafe_target"),
        ("http://169.254.169.254/", ["93.184.216.34"], "unsafe_target"),
        ("http://10.1.1.1/", ["93.184.216.34"], "unsafe_target"),
        ("https://public.example/", ["93.184.216.34", "192.168.1.1"], "unsafe_target"),
        ("file:///etc/passwd", [], "invalid_url"),
        ("https://u:p@example.com/", [], "invalid_url"),
        ("https://example.com:8443/", [], "invalid_url"),
        ("https://example.com\\@127.0.0.1/", [], "invalid_url"),
    ],
)
def test_unsafe_targets_never_reach_network(url, addresses, code):
    async def scenario():
        wire = WireBackend()

        async def dns(host, port):
            return addresses

        client = FreeWebClient(resolver=dns, network_backend=wire)
        with pytest.raises(WebSourceError, match=f"^{code}$"):
            await client.fetch(url)
        assert wire.connects == []
        await client.aclose()

    asyncio.run(scenario())


def test_malformed_redirect_is_safe_and_stops_before_second_connect():
    async def scenario():
        wire = WireBackend(response(status=302, headers={"Location": "http://["}))
        events = []
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        try:
            with pytest.raises(WebSourceError, match="^invalid_url$"):
                await client.fetch(
                    "https://example.com/", observer=lambda k, p: events.append((k, p))
                )
            assert wire.connects == [("93.184.216.34", 443)]
            assert [kind for kind, _ in events] == ["request.started", "request.completed"]
            assert events[-1][1]["status_code"] == 302
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_redirect_is_followed_then_private_target_rejected():
    async def scenario():
        wire = WireBackend(
            response(status=302, headers={"Location": "/next"}),
            response(status=302, headers={"Location": "http://10.0.0.1/"}),
        )
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        events = []
        with pytest.raises(WebSourceError, match="^unsafe_target$"):
            await client.fetch("https://example.com/", observer=lambda k, p: events.append((k, p)))
        assert wire.connects == [("93.184.216.34", 443)] * 2
        assert [p["url"] for k, p in events if k == "request.started"] == [
            "https://example.com/",
            "https://example.com/next",
        ]
        await client.aclose()

    asyncio.run(scenario())


def test_redirect_request_timings_are_not_cumulative():
    async def scenario():
        now = [0.0]

        class TimedBackend(WireBackend):
            async def connect_tcp(self, *args, **kwargs):
                now[0] += 2.0
                return await super().connect_tcp(*args, **kwargs)

        wire = TimedBackend(response(status=302, headers={"Location": "/final"}), response())
        events = []
        client = FreeWebClient(resolver=public_dns, network_backend=wire, clock=lambda: now[0])
        page = await client.fetch(
            "https://example.com/", observer=lambda kind, payload: events.append((kind, payload))
        )
        completed = [payload for kind, payload in events if kind == "request.completed"]
        assert len(wire.connects) == 2
        assert [item["elapsed_ms"] for item in completed] == [2000.0, 2000.0]
        assert page["elapsed_ms"] == 4000.0
        await client.aclose()

    asyncio.run(scenario())


def test_redirect_request_limit_and_decompressed_body_bound():
    import gzip

    async def scenario():
        wire = WireBackend(
            *[response(status=302, headers={"Location": "/again"}) for _ in range(5)]
        )
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        with pytest.raises(WebSourceError, match="^redirect_limit$"):
            await client.fetch("https://example.com/")
        assert len(wire.connects) == 4
        await client.aclose()
        body = gzip.compress(b"a" * (2 * 1024 * 1024 + 1))
        wire = WireBackend(response(body, headers={"Content-Encoding": "gzip"}))
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        events = []
        with pytest.raises(WebSourceError, match="^body_too_large$"):
            await client.fetch("https://example.com/", observer=lambda k, p: events.append((k, p)))
        assert len(wire.connects) == 1
        assert events[-1][0] == "request.failed"
        assert events[-1][1]["error_code"] == "body_too_large"
        await client.aclose()

    asyncio.run(scenario())


def test_search_uses_only_yahoo_observes_python_request_and_ignores_proxy_env(monkeypatch):
    import os

    import httpx
    from ddgs.ddgs import DDGS

    monkeypatch.setenv("DDGS_PROXY", "not-a-proxy")
    original_threads = DDGS.threads
    native_calls, configs, events = [], [], []

    def handler(request):
        native_calls.append(request)
        return httpx.Response(
            200,
            text="""<html><div class="relsrch"><div class="Title"><h3>결과</h3>
            <a href="https://example.com/a">링크</a></div><div class="Text">설명</div></div></html>""",
        )

    def factory(**kwargs):
        configs.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    async def scenario():
        client = FreeWebClient(search_http_client_factory=factory)
        result = await client.search("질문", observer=lambda k, p: events.append((k, p)))
        assert result["results"] == [
            {"title": "결과", "url": "https://example.com/a", "snippet": "설명"}
        ]
        assert result["backend"] == "yahoo" and result["observed_requests"] == 1
        assert [k for k, p in events] == ["request.started", "request.completed"]
        assert events[0][1]["operation"] == "search" and "?p=" in events[0][1]["url"]
        assert len(native_calls) == 1 and native_calls[0].url.host == "search.yahoo.com"
        assert configs[0]["trust_env"] is False and configs[0]["verify"] is True
        assert configs[0]["timeout"] == 12 and configs[0]["follow_redirects"] is False
        assert os.environ["DDGS_PROXY"] == "not-a-proxy" and DDGS.threads == original_threads
        await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["search", "fetch"])
def test_rate_limit_has_safe_event_and_cooldown_without_retry(operation):
    import httpx

    calls, events = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"}, text="provider-secret-error")

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    async def scenario():
        wire = WireBackend(response(b"provider-secret-error", 429, {"Retry-After": "120"}))
        client = FreeWebClient(
            resolver=public_dns, network_backend=wire, search_http_client_factory=factory
        )
        invoke = client.search if operation == "search" else client.fetch
        arg = "question" if operation == "search" else "https://example.com/"
        with pytest.raises(WebSourceError, match="^rate_limited$"):
            await invoke(arg, observer=lambda k, p: events.append((k, p)))
        with pytest.raises(WebSourceError, match="^cooldown_active$"):
            await invoke(arg)
        assert len(calls if operation == "search" else wire.connects) == 1
        assert [k for k, p in events] == ["request.started", "request.failed"]
        assert events[-1][1]["error_code"] == "rate_limited"
        assert events[-1][1]["retry_after_seconds"] == 120
        assert "provider-secret-error" not in repr(events)
        await client.aclose()

    asyncio.run(scenario())


def test_cancelled_search_keeps_native_ownership_and_enforces_gap():
    import threading

    import httpx

    entered, release = threading.Event(), threading.Event()
    calls, times, sleeps = [], [], []
    clock = [10.0]

    def handler(request):
        calls.append(request)
        times.append(clock[0])
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return httpx.Response(200, text="<html><p>No results</p></html>")

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    async def scenario():
        client = FreeWebClient(
            search_http_client_factory=factory, clock=lambda: clock[0], sleep=sleep
        )
        first = asyncio.create_task(client.search("first"))
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(client.search("second"))
        await asyncio.sleep(0.03)
        assert len(calls) == 1
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0.03)
        assert not closing.done()
        release.set()
        await second
        await closing
        assert times == [10.0, 12.0]
        assert sleeps == [2.0]
        with pytest.raises(WebSourceError, match="^client_closed$"):
            await client.search("closed")

    try:
        asyncio.run(scenario())
    finally:
        release.set()


@pytest.mark.parametrize("operation", ["search", "fetch"])
def test_observer_failure_is_before_transmission(operation):
    import httpx

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="<html></html>")

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    def observer(kind, payload):
        raise RuntimeError("private callback details")

    async def scenario():
        wire = WireBackend()
        client = FreeWebClient(
            resolver=public_dns, network_backend=wire, search_http_client_factory=factory
        )
        invoke = client.search if operation == "search" else client.fetch
        with pytest.raises(WebSourceError, match="^observer_failed$"):
            await invoke(
                "q" if operation == "search" else "https://example.com/", observer=observer
            )
        assert calls == [] and wire.connects == []
        await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["search", "fetch"])
def test_challenge_document_is_not_an_article_mentioning_captcha(operation):
    import httpx

    calls = []
    bodies = [
        ARTICLE.replace("공개된".encode(), b"captcha "),
        b'<html><head><title>Just a moment...</title></head><body><script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script></body></html>',
    ]

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=bodies[len(calls) - 1])

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    async def scenario():
        clock = [100.0]

        async def sleep(delay):
            clock[0] += delay

        wire = WireBackend(*[response(body) for body in bodies])
        client = FreeWebClient(
            resolver=public_dns,
            network_backend=wire,
            search_http_client_factory=factory,
            clock=lambda: clock[0],
            sleep=sleep,
        )
        invoke = client.search if operation == "search" else client.fetch
        arg = "q" if operation == "search" else "https://example.com/"
        result = await invoke(arg)
        if operation == "fetch":
            assert "captcha" in result["text"]
        with pytest.raises(WebSourceError, match="^challenge_detected$"):
            await invoke(arg)
        with pytest.raises(WebSourceError, match="^cooldown_active$"):
            await invoke(arg)
        assert len(calls if operation == "search" else wire.connects) == 2
        await client.aclose()

    asyncio.run(scenario())


def test_fetch_timeout_is_safe_and_no_retry():
    class TimeoutBackend(WireBackend):
        async def connect_tcp(self, *args, **kwargs):
            self.connects.append(args)
            raise httpcore.ConnectTimeout("private-host-error")

    async def scenario():
        wire = TimeoutBackend()
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        events = []
        with pytest.raises(WebSourceError, match="^fetch_timeout$"):
            await client.fetch("https://example.com/", observer=lambda k, p: events.append((k, p)))
        assert len(wire.connects) == 1
        assert events[-1][1]["error_code"] == "fetch_timeout"
        assert "private-host-error" not in repr(events)
        await client.aclose()

    asyncio.run(scenario())


def test_fetch_partial_extraction_has_explicit_warning_and_date_not_publication():
    async def scenario():
        # 실제 Trafilatura를 실행하며 본문 상한을 넘는 공개형 문서를 입력한다.
        text = "연구 자료의 결과와 의미를 설명하는 긴 문장입니다. " * 9000
        body = (
            '<html><head><title>긴 문서</title><meta property="article:published_time" '
            'content="2001-01-01"></head><body><article><p>' + text + "</p></article></body></html>"
        ).encode()
        assert len(body) < 2 * 1024 * 1024
        wire = WireBackend(response(body))
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        result = await client.fetch("https://example.com/")
        assert len(result["text"]) == 200000
        assert "extracted_text_truncated" in result["warnings"]
        assert result["extracted_date_candidate"] == "2001-01-01" and result["published_at"] is None
        await client.aclose()

    asyncio.run(scenario())


def test_non_html_and_dns_timeout_are_safe_without_extraction():
    async def scenario():
        wire = WireBackend(response(b"%PDF-1.7", headers={"Content-Type": "application/pdf"}))
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        with pytest.raises(WebSourceError, match="^unsupported_content_type$"):
            await client.fetch("https://example.com/")
        await client.aclose()

        async def timeout_dns(host, port):
            raise TimeoutError("resolver-private-details")

        wire = WireBackend()
        client = FreeWebClient(resolver=timeout_dns, network_backend=wire)
        with pytest.raises(WebSourceError, match="^fetch_timeout$"):
            await client.fetch("https://example.com/")
        assert wire.connects == []
        await client.aclose()

    asyncio.run(scenario())


def test_search_setup_error_and_timeout_never_leak_provider_error():
    import httpx

    async def scenario():
        def broken_factory(**kwargs):
            raise RuntimeError("private-constructor-error")

        client = FreeWebClient(ddgs_factory=broken_factory)
        with pytest.raises(WebSourceError, match="^search_failed$"):
            await client.search("question")
        await client.aclose()
        calls, events = [], []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("private-timeout-error")

        def factory(**kwargs):
            return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

        client = FreeWebClient(search_http_client_factory=factory)
        with pytest.raises(WebSourceError, match="^search_timeout$"):
            await client.search("question", observer=lambda k, p: events.append((k, p)))
        assert len(calls) == 1
        assert events[-1][1]["error_code"] == "search_timeout"
        assert "private-timeout-error" not in repr(events)
        await client.aclose()

    asyncio.run(scenario())


def test_cancelled_queued_search_never_transmits():
    import threading

    import httpx

    entered, release = threading.Event(), threading.Event()
    calls = []

    def handler(request):
        calls.append(request)
        entered.set()
        assert release.wait(2)
        return httpx.Response(200, text="<html>No results</html>")

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    async def scenario():
        client = FreeWebClient(
            search_http_client_factory=factory, sleep=lambda delay: asyncio.sleep(0)
        )
        first = asyncio.create_task(client.search("first"))
        assert await asyncio.to_thread(entered.wait, 2)
        queued = asyncio.create_task(client.search("cancelled-before-slot"))
        await asyncio.sleep(0.01)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        release.set()
        await first
        await client.aclose()
        assert len(calls) == 1

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_native_timeout_returns_but_keeps_thread_owned(monkeypatch):
    import threading

    import httpx

    entered, release = threading.Event(), threading.Event()
    original_timeout = asyncio.timeout
    deadlines = []

    def timeout(seconds):
        deadlines.append(seconds)
        return original_timeout(0.03)

    monkeypatch.setattr(asyncio, "timeout", timeout)

    def handler(request):
        entered.set()
        release.wait(0.2)
        return httpx.Response(200, text="<html>No results</html>")

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    async def scenario():
        client = FreeWebClient(search_http_client_factory=factory)
        with pytest.raises(WebSourceError, match="^search_timeout$"):
            await client.search("slow")
        assert entered.is_set() and deadlines == [12]
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0.01)
        assert not closing.done()
        release.set()
        await closing

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_dns_is_inside_fifteen_second_request_deadline(monkeypatch):
    original_timeout = asyncio.timeout
    timeouts = []

    def fast_timeout(seconds):
        timeouts.append(seconds)
        return original_timeout(min(seconds, 0.005))

    monkeypatch.setattr(asyncio, "timeout", fast_timeout)

    async def slow_dns(host, port):
        await asyncio.sleep(0.05)
        return ["93.184.216.34"]

    async def scenario():
        wire = WireBackend()
        client = FreeWebClient(resolver=slow_dns, network_backend=wire)
        with pytest.raises(WebSourceError, match="^fetch_timeout$"):
            await client.fetch("https://example.com/")
        assert wire.connects == []
        assert timeouts[0] == 15
        await client.aclose()

    asyncio.run(scenario())


def test_cancelling_aclose_does_not_cancel_owned_native_search():
    import threading

    import httpx

    entered, release = threading.Event(), threading.Event()

    def handler(request):
        entered.set()
        release.wait(1)
        return httpx.Response(200, text="<html>No results</html>")

    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    async def scenario():
        client = FreeWebClient(search_http_client_factory=factory)
        first = asyncio.create_task(client.search("first"))
        assert await asyncio.to_thread(entered.wait, 1)
        close = asyncio.create_task(client.aclose())
        await asyncio.sleep(0.01)
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close
        await asyncio.sleep(0.01)
        assert not first.done()
        release.set()
        assert (await first)["results"] == []
        await client.aclose()

    try:
        asyncio.run(scenario())
    finally:
        release.set()


@pytest.mark.parametrize("stage", ["executor", "initialization", "observer", "transport"])
@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_logically_finished_search_cannot_start_late_http(monkeypatch, stage, ending):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import httpx
    from ddgs.ddgs import DDGS

    entered, release = threading.Event(), threading.Event()
    calls = []
    original_timeout = asyncio.timeout
    if ending == "timeout":
        monkeypatch.setattr(asyncio, "timeout", lambda seconds: original_timeout(0.08))

    def pause():
        entered.set()
        assert release.wait(5)

    def sdk_factory(**kwargs):
        if stage == "initialization":
            pause()
        return DDGS(**kwargs)

    def observer(kind, payload):
        if stage == "observer" and kind == "request.started":
            pause()

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="<html>No results</html>")

    class DelayedClient(httpx.Client):
        def send(self, *args, **kwargs):
            if stage == "transport":
                pause()
            return super().send(*args, **kwargs)

    def factory(**kwargs):
        return DelayedClient(transport=httpx.MockTransport(handler), **kwargs)

    async def scenario():
        loop = asyncio.get_running_loop()
        blocker = None
        if stage == "executor":
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            blocker = loop.run_in_executor(None, pause)
        client = FreeWebClient(ddgs_factory=sdk_factory, search_http_client_factory=factory)
        first = asyncio.create_task(client.search("late", observer=observer))
        try:
            for _ in range(1000):
                if entered.is_set() and client._native:
                    break
                await asyncio.sleep(0.001)
            assert entered.is_set() and client._native
            assert calls == []
            if ending == "cancel":
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            else:
                with pytest.raises(WebSourceError, match="^search_timeout$"):
                    await first
            closing = asyncio.create_task(client.aclose())
            await asyncio.sleep(0.005)
            assert not closing.done(), "native resource ownership must survive logical exit"
            assert client._search_lock.locked()
            release.set()
            await closing
            if blocker is not None:
                await blocker
            assert calls == [], "cancelled/expired call transmitted its first HTTP later"
            assert not client._owned and not client._native
        finally:
            release.set()
            await client.aclose()

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_zero_port_rejected_and_idna_host_keeps_wire_identity():
    async def scenario():
        wire = WireBackend()
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        with pytest.raises(WebSourceError, match="^invalid_url$"):
            await client.fetch("https://example.com:0/")
        assert wire.connects == []
        page = await client.fetch("https://faß.de/경로")
        assert page["requested_url"] == "https://faß.de/경로"
        assert wire.tls[0][1] == "xn--fa-hia.de"
        assert b"xn--fa-hia.de" in b"".join(wire.writes)
        await client.aclose()

    asyncio.run(scenario())


def test_fetch_pins_ip_preserves_host_sni_and_original_unicode_url():
    async def scenario():
        wire = WireBackend()
        client = FreeWebClient(resolver=public_dns, network_backend=wire)
        events = []
        url = "https://example.com/한글?q=자료"
        page = await client.fetch(url, observer=lambda k, p: events.append((k, p)))
        assert page.get("requested_url") == url
        assert page["final_url"] == url
        assert "공개된 연구 결과" in page["text"]
        assert page["published_at"] is None
        assert page["text_sha256"] == hashlib.sha256(page["text"].encode()).hexdigest()
        assert page["html_sha256"] == hashlib.sha256(ARTICLE).hexdigest()
        assert wire.connects == [("93.184.216.34", 443)]
        context, sni = wire.tls[0]
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert sni == "example.com"
        request = b"".join(wire.writes)
        assert b"Host: example.com" in request or b"host: example.com" in request
        assert b"/%ED%95%9C%EA%B8%80?q=%EC%9E%90%EB%A3%8C" in request
        assert [k for k, p in events] == ["request.started", "request.completed"]
        assert events[0][1]["web_request_id"] == events[1][1]["web_request_id"]
        assert set(events[0][1]) == {"operation", "web_request_id", "method", "url", "started_at"}
        await client.aclose()

    asyncio.run(scenario())
