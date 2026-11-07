"""무료 웹 자료의 외부 I/O 경계. 전역 SDK/환경 변경 없이 인스턴스를 소유한다."""

# ruff: noqa: BLE001
# 신뢰 경계에서 SDK/추출기/사용자 callback의 임의 예외를 안전 코드로 바꾼다.
# CancelledError(BaseException)는 잡지 않는다.

import asyncio
import contextlib
import hashlib
import ipaddress
import socket
import ssl
import threading
import time
import zlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpcore
import httpx
import trafilatura

from 서버.야후결과파서 import extract_algo_results, looks_like_results_html
from 서버.웹계약 import WebSourceError

# 검색 한 건이 전송 슬롯을 얻은 뒤 쓸 수 있는 시간(초). _search_sync의 native timeout(12)과 같은 값이다.
SEARCH_TIMEOUT_SECONDS = 12
# 직렬화 대기열에서 슬롯을 기다릴 수 있는 시간(초). 동시에 온 검색은 2초 간격으로 순서대로 처리되므로
# 이 한도는 앞선 native 검색이 멈췄을 때의 무한 대기만 막는다. 대기 시간은 위 마감에 넣지 않는다.
SEARCH_QUEUE_LIMIT_SECONDS = 120

SAFE_ERROR_CODES = frozenset(
    {
        "fetch_timeout",
        "fetch_failed",
        "search_timeout",
        "search_failed",
        "search_unparsed",
        "rate_limited",
        "challenge_detected",
        "cooldown_active",
        "http_error",
        "invalid_url",
        "unsafe_target",
        "dns_failed",
        "redirect_limit",
        "body_too_large",
        "unsupported_content_type",
        "extraction_failed",
        "empty_content",
        "observer_failed",
        "client_closed",
        "search_backend_unavailable",
        "invalid_query",
    }
)


def _safe_error(exc, operation):
    if isinstance(exc, WebSourceError) and str(exc) in SAFE_ERROR_CODES:
        return str(exc)
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, httpx.TimeoutException, httpcore.TimeoutException)):
            return operation + "_timeout"
        current = current.__cause__
    return operation + "_failed"


def _now():
    return datetime.now(UTC).isoformat()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


async def _resolve(host, port):
    rows = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(row[4][0] for row in rows))


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, host, ip, backend):
        self.host, self.ip, self.backend = host, ip, backend

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self.host:
            raise WebSourceError("unsafe_target")
        return await self.backend.connect_tcp(
            self.ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _CoreStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        async for chunk in self.stream:
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class _PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, host, ip, backend):
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            retries=0,
            max_connections=1,
            network_backend=_PinnedBackend(host, ip, backend),
        )

    async def handle_async_request(self, request):
        core_request = httpcore.Request(
            request.method,
            httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        response = await self.pool.handle_async_request(core_request)
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_CoreStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.pool.aclose()


class _SearchGate:
    """논리 수명은 native 자원 소유권과 별개다. 전송 진입과 취소를 직렬화한다."""

    def __init__(self, clock):
        # 대기열에서 순서를 기다리는 동안에는 마감이 없다. 전송 슬롯을 얻은 뒤 start()가 정한다.
        self.clock, self.deadline = clock, float("inf")
        self.lock = threading.Lock()
        self.cancelled = False

    def start(self, seconds):
        with self.lock:
            self.deadline = self.clock() + seconds

    def cancel(self):
        with self.lock:
            self.cancelled = True

    def check(self):
        with self.lock:
            if self.cancelled or self.clock() >= self.deadline:
                raise WebSourceError("search_timeout")


class _SearchTransport(httpx.BaseTransport):
    def __init__(self, transport, gate):
        self.transport, self.gate = transport, gate

    def handle_request(self, request):
        # callback/SDK/client.send의 지연 뒤, native HTTP 경계에 진입하는 마지막 허가.
        # 이 지점을 지난 요청은 취소로 소유권을 놓지 않고 실제 종료까지 기다린다.
        self.gate.check()
        return self.transport.handle_request(request)


class FreeWebClient:
    def __init__(
        self,
        *,
        resolver=None,
        network_backend=None,
        ddgs_factory=None,
        search_http_client_factory=None,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ):
        # None이면 DDGS 기본 전송(primp)을 쓴다. 주입하면 그 httpx 전송으로 바꾼다(시험·강화 구성).
        self._search_http_client_factory = search_http_client_factory
        self._resolver = resolver or _resolve
        self._network_backend = network_backend or httpcore.AnyIOBackend()
        self._ddgs_factory = ddgs_factory
        self._clock, self._sleep = clock, sleep
        self._owned = set()
        self._native = set()
        self._closed = False
        self._search_lock = asyncio.Lock()
        self._next_search = 0.0
        self._cooldown_until = 0.0
        self._fetch_cooldowns = {}

    @staticmethod
    def _retry_after(headers):
        value = headers.get("retry-after", "")
        try:
            return max(0.0, float(int(value)))
        except (ValueError, TypeError):
            try:
                return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return 60.0

    @staticmethod
    def _http_error(status, content):
        if status == 429:
            return "rate_limited"
        if status == 403:
            return "challenge_detected"
        if status < 200 or status >= 300:
            return "http_error"
        from lxml import html

        try:
            tree = html.fromstring(content)
            title = " ".join(tree.xpath("//title/text()")).strip().lower()
            active_challenge = tree.xpath(
                '//script[contains(@src,"/cdn-cgi/challenge-platform/")] | '
                '//form[@id="challenge-form"] | //div[@class="h-captcha"] | '
                '//div[@class="g-recaptcha"]'
            )
            if active_challenge and title.startswith(
                ("just a moment", "attention required", "verify you are human", "access denied")
            ):
                return "challenge_detected"
        except (ValueError, TypeError, html.etree.ParserError):
            pass
        return None

    def _emit(self, observer, kind, payload):
        if observer:
            try:
                observer(kind, dict(payload))
            except Exception:
                raise WebSourceError("observer_failed") from None

    async def _own(self, coroutine):
        if self._closed:
            coroutine.close()
            raise WebSourceError("client_closed")
        task = asyncio.create_task(coroutine)
        self._owned.add(task)

        def finished(done):
            self._owned.discard(done)
            if not done.cancelled():
                done.exception()  # 취소된 호출자의 늦은 실패도 회수한다.

        task.add_done_callback(finished)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task not in self._native:
                task.cancel()  # 대기 중 검색과 비동기 fetch는 새 전송 없이 정리한다.
            raise

    async def fetch(self, url, *, observer=None):
        return await self._own(self._fetch(url, observer))

    async def _target(self, url):
        try:
            if not isinstance(url, str) or any(ord(c) < 33 for c in url) or "\\" in url:
                raise ValueError
            parsed = urlsplit(url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError
            port = (
                parsed.port
                if parsed.port is not None
                else (443 if parsed.scheme == "https" else 80)
            )
            if port not in (80, 443) or "%" in parsed.hostname:
                raise ValueError
            # HTTPX의 IDNA2008 wire host를 DNS/TCP/SNI에 일관되게 쓴다.
            # stdlib IDNA2003와 비교하면 faß.de 같은 유효 공개 호스트를 오거절한다.
            host = httpx.URL(url).raw_host.decode("ascii")
        except (ValueError, UnicodeError, httpx.InvalidURL):
            raise WebSourceError("invalid_url") from None
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [str(literal)]
        else:
            try:
                addresses = await self._resolver(host, port)
            except TimeoutError:
                raise WebSourceError("fetch_timeout") from None
            except Exception:
                raise WebSourceError("dns_failed") from None
        if not addresses:
            raise WebSourceError("dns_failed")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                raise WebSourceError("unsafe_target") from None
            mapped = getattr(ip, "ipv4_mapped", None)
            if (
                not ip.is_global
                or ip.is_multicast
                or ip.is_reserved
                or getattr(ip, "is_site_local", False)
                or (mapped and not mapped.is_global)
            ):
                raise WebSourceError("unsafe_target")
        return host, port, addresses[0]

    async def _body(self, response):
        limit = 2 * 1024 * 1024
        encoding = response.headers.get("content-encoding", "identity").lower()
        if encoding not in ("identity", "gzip", "deflate"):
            raise WebSourceError("unsupported_content_type")
        decoder = (
            zlib.decompressobj(31 if encoding == "gzip" else 15) if encoding != "identity" else None
        )
        content, received = bytearray(), 0
        async for chunk in response.aiter_raw():
            received += len(chunk)
            if received > limit:
                raise WebSourceError("body_too_large")
            decoded = decoder.decompress(chunk, limit - len(content) + 1) if decoder else chunk
            if len(content) + len(decoded) > limit:
                raise WebSourceError("body_too_large")
            content.extend(decoded)
        if decoder and (not decoder.eof or decoder.unused_data):
            raise WebSourceError("fetch_failed")
        return bytes(content)

    async def _fetch(self, url, observer):
        started = self._clock()
        original, current, count = url, url, 0
        for attempt in range(4):
            deadline = asyncio.get_running_loop().time() + 15
            try:
                async with asyncio.timeout(15):
                    host, _port, ip = await self._target(current)
            except TimeoutError:
                raise WebSourceError("fetch_timeout") from None
            if self._fetch_cooldowns.get(host, 0.0) > self._clock():
                raise WebSourceError("cooldown_active")
            transport = _PinnedTransport(host, ip, self._network_backend)
            payload = {
                "operation": "fetch",
                "web_request_id": uuid4().hex,
                "method": "GET",
                "url": current,
                "started_at": _now(),
            }
            self._emit(observer, "request.started", payload)
            status, retry_after = None, None
            request_started = self._clock()
            try:
                async with asyncio.timeout(max(0.0, deadline - asyncio.get_running_loop().time())):
                    async with (
                        httpx.AsyncClient(
                            transport=transport, trust_env=False, timeout=15, follow_redirects=False
                        ) as client,
                        client.stream(
                            "GET", current, headers={"Accept-Encoding": "identity"}
                        ) as response,
                    ):
                        status, location = (
                            response.status_code,
                            response.headers.get("location"),
                        )
                        if status in (429, 403):
                            retry_after = self._retry_after(response.headers)
                            self._fetch_cooldowns[host] = self._clock() + max(60.0, retry_after)
                            raise WebSourceError(self._http_error(status, b""))
                        if status not in (301, 302, 303, 307, 308):
                            media = (
                                response.headers.get("content-type", "")
                                .split(";", 1)[0]
                                .strip()
                                .lower()
                            )
                            if media and media not in (
                                "text/html",
                                "application/xhtml+xml",
                                "text/plain",
                            ):
                                raise WebSourceError("unsupported_content_type")
                        content = await self._body(response)
                        if status not in (301, 302, 303, 307, 308):
                            code = self._http_error(status, content)
                            if code:
                                if code == "challenge_detected":
                                    retry_after = self._retry_after(response.headers)
                                    self._fetch_cooldowns[host] = self._clock() + max(
                                        60.0, retry_after
                                    )
                                raise WebSourceError(code)
            except Exception as exc:
                code = _safe_error(exc, "fetch")
                self._emit(
                    observer,
                    "request.failed",
                    payload
                    | {
                        "error_code": code,
                        "status_code": status,
                        "retry_after_seconds": retry_after,
                        "elapsed_ms": (self._clock() - request_started) * 1000,
                    },
                )
                raise WebSourceError(code) from None
            count += 1
            self._emit(
                observer,
                "request.completed",
                payload
                | {
                    "status_code": status,
                    "elapsed_ms": (self._clock() - request_started) * 1000,
                },
            )
            if status in (301, 302, 303, 307, 308):
                if not location:
                    raise WebSourceError("invalid_url")
                try:
                    current = urljoin(current, location)
                except ValueError:
                    raise WebSourceError("invalid_url") from None
                continue
            break
        else:
            raise WebSourceError("redirect_limit")
        try:
            document = trafilatura.bare_extraction(content, url=current, with_metadata=True)
        except Exception:
            raise WebSourceError("extraction_failed") from None
        if document is None or not document.text:
            raise WebSourceError("empty_content")
        warnings = ["extraction_not_verified"]
        if len(document.text) > 200000:
            warnings.append("extracted_text_truncated")
        text = document.text[:200000]
        return {
            "requested_url": original,
            "final_url": current,
            "title": document.title or "",
            "text": text,
            "text_sha256": _hash(text.encode()),
            "html_sha256": _hash(content),
            "fetched_at": _now(),
            "published_at": None,
            "extracted_date_candidate": document.date,
            "warnings": warnings,
            "elapsed_ms": (self._clock() - started) * 1000,
            "observed_requests": count,
        }

    async def search(self, query, *, observer=None):
        gate = _SearchGate(self._clock)
        try:
            # 순서를 기다리는 동안에는 대기 한도만 적용한다. 슬롯을 얻으면 _search가 timer를
            # 검색 마감(SEARCH_TIMEOUT_SECONDS)으로 다시 건다. 마감은 호출자 쪽에 둬야 native
            # 스레드가 끝날 때까지 소유권(_own)을 유지하면서도 논리 호출만 먼저 끝낼 수 있다.
            async with asyncio.timeout(SEARCH_QUEUE_LIMIT_SECONDS) as timer:
                return await self._own(self._search(query, observer, gate, timer))
        except TimeoutError:
            raise WebSourceError("search_timeout") from None
        finally:
            gate.cancel()

    async def _search(self, query, observer, gate, timer):
        started = self._clock()
        async with self._search_lock:
            if self._cooldown_until > self._clock():
                raise WebSourceError("cooldown_active")
            delay = self._next_search - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self._next_search = self._clock() + 2.0
            owner = asyncio.current_task()
            self._native.add(owner)
            try:
                if timer.expired():
                    # 대기 한도가 같은 순간에 끝났다. 호출자는 이미 종료됐으므로 전송하지 않는다.
                    raise WebSourceError("search_timeout")
                gate.start(SEARCH_TIMEOUT_SECONDS)
                timer.reschedule(asyncio.get_running_loop().time() + SEARCH_TIMEOUT_SECONDS)
                result, count = await asyncio.to_thread(self._search_sync, query, observer, gate)
            except Exception as exc:
                raise WebSourceError(_safe_error(exc, "search")) from None
            finally:
                self._native.discard(owner)
                self._next_search = self._clock() + 2.0
        return {
            "query": query,
            "backend": "yahoo",
            "results": result,
            "elapsed_ms": (self._clock() - started) * 1000,
            "observed_requests": count,
            "warnings": ["python_http_boundary_only"]
            + ([] if self._search_http_client_factory else ["native_redirects_unobserved"]),
        }

    def _search_sync(self, query, observer, gate):
        gate.check()
        from ddgs.ddgs import DDGS, ENGINES
        from ddgs.exceptions import DDGSException

        engine_class = ENGINES.get("text", {}).get("yahoo")
        if engine_class is None or engine_class.disabled:
            raise WebSourceError("search_backend_unavailable")
        sdk = (self._ddgs_factory or DDGS)(timeout=12, verify=True)
        # DDGS.__init__만 환경 proxy를 읽는다. engine 생성 전에 명시적으로 끊는다.
        gate.check()
        sdk._proxy = None
        engines = sdk._get_engines("text", "yahoo")
        if len(engines) != 1 or engines[0].name != "yahoo" or engines[0].disabled:
            raise WebSourceError("search_backend_unavailable")
        sdk._get_engines = lambda category, backend: engines
        engine = engines[0]
        count, failures, pages = [], [], []
        # 기본값은 DDGS 기본 전송(primp)이다. 2026-11-07 실측에서 같은 요청이 primp는 HTTP200,
        # httpx 기본 헤더는 HTTP500(본문 0바이트)이었다(검증결과/웹진단실측_결과.json).
        # 주입한 httpx 전송은 그것으로 바꾸고 환경 proxy·자동 redirect를 쓰지 않는다(시험·강화 구성).
        # primp에는 trust_env가 없고 follow_redirects 기본값이 True라 기본 전송의 redirect는 관측하지 못한다.
        with contextlib.ExitStack() as stack:
            if self._search_http_client_factory is not None:
                client = stack.enter_context(
                    self._search_http_client_factory(
                        timeout=12, verify=True, trust_env=False, follow_redirects=False
                    )
                )
                # HTTPX의 인스턴스별 transport 선택을 감싼다. SDK/global 상태는 불변.
                # shortcut: httpx 0.28.1의 비공개 _transport_for_url에 결합한다.
                # httpx 갱신 때 test_logically_finished_search_cannot_start_late_http가 먼저 깨진다.
                select_transport = client._transport_for_url
                client._transport_for_url = lambda url: _SearchTransport(
                    select_transport(url), gate
                )
                engine.http_client.client = client
            original = engine.http_client.request
            original_extract = engine.extract_results

            def extract_results(html_text):
                # ddgs 9.16.1은 신규 section.s-algo 레이아웃을 읽지 못해 기존 파서가 비면 보강한다.
                return original_extract(html_text) or extract_algo_results(engine, html_text)

            engine.extract_results = extract_results

            def request(method, url, *args, **kwargs):
                payload = {
                    "operation": "search",
                    "web_request_id": uuid4().hex,
                    "method": method,
                    "url": str(httpx.Request(method, url, params=kwargs.get("params")).url),
                    "started_at": _now(),
                }
                request_started = self._clock()
                status, retry_after, transmitted = None, None, False
                try:
                    gate.check()
                    self._emit(observer, "request.started", payload)
                    gate.check()
                    count.append(1)
                    transmitted = True
                    response = original(method, url, *args, **kwargs)
                    status = response.status_code
                    code = self._http_error(status, response.content)
                    if code:
                        if code in ("rate_limited", "challenge_detected"):
                            retry_after = self._retry_after(response._resp.headers)
                            self._cooldown_until = self._clock() + max(60.0, retry_after)
                        raise WebSourceError(code)
                    pages.append(response.text)
                    self._emit(
                        observer,
                        "request.completed",
                        payload
                        | {
                            "status_code": status,
                            "elapsed_ms": (self._clock() - request_started) * 1000,
                        },
                    )
                    return response
                except Exception as exc:
                    code = _safe_error(exc, "search")
                    failures.append(code)
                    if transmitted:
                        self._emit(
                            observer,
                            "request.failed",
                            payload
                            | {
                                "status_code": status,
                                "error_code": code,
                                "retry_after_seconds": retry_after,
                                "elapsed_ms": (self._clock() - request_started) * 1000,
                            },
                        )
                    raise WebSourceError(code) from None

            engine.http_client.request = request
            try:
                rows = sdk.text(query, backend="yahoo", max_results=10)
            except DDGSException as exc:
                if failures:
                    raise WebSourceError(failures[0]) from None
                if str(exc) != "No results found.":
                    raise WebSourceError("search_failed") from None
                # DDGS는 차단·파서 불일치·진짜 빈 결과를 같은 문구로 보고한다. 200 응답이 결과 페이지처럼
                # 보이는데 0건이면 빈 결과가 아니라 파서 불일치로 구별한다.
                if pages and looks_like_results_html(pages[-1]):
                    raise WebSourceError("search_unparsed") from None
                rows = []
            if failures:
                raise WebSourceError(failures[0])
        return [
            {
                "title": str(row.get("title", "")),
                "url": str(row.get("href", "")),
                "snippet": str(row.get("body", "")),
            }
            for row in rows
        ], len(count)

    async def aclose(self):
        self._closed = True
        while self._owned:
            await asyncio.shield(asyncio.gather(*list(self._owned), return_exceptions=True))
