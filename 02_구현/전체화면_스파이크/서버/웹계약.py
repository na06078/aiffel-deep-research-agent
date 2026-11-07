"""무료 웹 자료 연결 정본. 메인 소유이며 구현 작업자는 읽기 전용으로 사용한다."""

from collections.abc import Callable
from typing import Any, Literal, NotRequired, Protocol, TypedDict

SourceMode = Literal["local", "web"]
SOURCE_PREFIXES: dict[str, SourceMode] = {
    "/웹": "web",
    "/디비": "local",
    "/DB": "local",
    "/데이터셋": "local",
}
MAX_WEB_SECTIONS = 4
MAX_WEB_ROUNDS = 2
MAX_WEB_SEARCH_ATTEMPTS = 16
MAX_WEB_FETCH_ATTEMPTS = 24
MAX_WEB_READ_CHARACTERS = 20000
MAX_WEB_NOTE_CHARACTERS = 6000
# 모델에게 요청하는 메모 길이. 모델은 글자 수를 정확히 세지 못하므로 하드 상한보다 낮게 요청한다.
# 2026-11-07 실사용에서 "6000자 이하"를 요청한 메모 9건이 3,064~6,156자(중앙 4,536자)였다.
WEB_NOTE_TARGET_CHARACTERS = 4000
MAX_WEB_HTML_BYTES = 2 * 1024 * 1024
MAX_WEB_EXTRACTED_CHARACTERS = 200000
WebObserver = Callable[[str, dict[str, Any]], None]


class WebSourceError(Exception):
    """안전한 코드 문자열만 담는다. 제공자 원문 오류를 사용자에게 전달하지 않는다."""


class ParsedCommand(TypedDict):
    raw_text: str
    text: str
    queued: bool
    source_mode: SourceMode
    mode_only: bool


class SearchHit(TypedDict):
    title: str
    url: str
    snippet: str


class SearchResponse(TypedDict):
    query: str
    backend: str
    results: list[SearchHit]
    elapsed_ms: float
    observed_requests: int
    warnings: NotRequired[list[str]]


class FetchedPage(TypedDict):
    requested_url: str
    final_url: str
    title: str
    text: str
    text_sha256: str
    html_sha256: str
    fetched_at: str
    published_at: None
    extracted_date_candidate: str | None
    warnings: list[str]
    elapsed_ms: float
    observed_requests: int


class SourceSnapshot(TypedDict):
    source_id: str
    run_id: str
    control_revision: int
    url: str
    final_url: str | None
    title: str
    snippet: str
    status: Literal["discovered", "ready", "failed"]
    text: str | None
    text_sha256: str | None
    html_sha256: str | None
    fetched_at: str | None
    published_at: None
    extracted_date_candidate: str | None
    warnings: list[str]
    error_code: str | None


class ReadClaim(TypedDict):
    source_id: str
    section_id: str
    model_call_id: str
    read_text_sha256: str
    note_sha256: str


class WebClient(Protocol):
    async def search(
        self, query: str, *, observer: WebObserver | None = None
    ) -> SearchResponse: ...

    async def fetch(self, url: str, *, observer: WebObserver | None = None) -> FetchedPage: ...

    async def aclose(self) -> None: ...


class WebStore(Protocol):
    def register_candidates(
        self, run_id: str, revision: int, hits: list[SearchHit]
    ) -> list[SourceSnapshot]: ...

    def get(self, run_id: str, revision: int, source_id: str) -> SourceSnapshot: ...

    def save_fetch(
        self, run_id: str, revision: int, source_id: str, page: FetchedPage
    ) -> SourceSnapshot: ...

    def mark_failed(self, run_id: str, revision: int, source_id: str, code: str) -> None: ...

    def mark_read(
        self,
        run_id: str,
        revision: int,
        source_id: str,
        section_id: str,
        model_call_id: str,
        read_text: str,
        note: str,
    ) -> None: ...

    def sources(self, run_id: str, revision: int) -> list[SourceSnapshot]: ...

    def read_claims(self, run_id: str, revision: int) -> list[ReadClaim]: ...
