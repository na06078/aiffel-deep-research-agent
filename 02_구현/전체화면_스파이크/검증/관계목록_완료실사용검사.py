"""문장·문단 구간/문맥 검토의 로컬 UI 종단 검사. 실제 제공자/인증/장부는 사용하지 않는다.

ScriptedModel의 정상 plan/select/read/draft/semantic 응답을 그대로 사용하며
종합 응답 내용만 합성 표/SVG로 바꾼다. 역사적 사실·실모델 의미 판별 증거 아님.
"""

import asyncio
import hashlib
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

I = Path(__file__).resolve().parents[1]
R = I.parents[1]
sys.path.insert(0, str(I))
sys.dont_write_bytecode = True
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(R / ".cache/playwright")
BASE = "http://127.0.0.1:8799"
LABEL = uuid.uuid4().hex
OUT = I / "검증결과/근거문맥/완료_실사용" / LABEL
DATA = R / ".cache/보고서품질_검증/실모델점검/근거문맥/완료UI" / LABEL
STATIC = R / ".cache/제출_화면"
PROOF = {
    "label": LABEL,
    "passed": False,
    "checks": [],
    "explicit_model_double": True,
    "actual_provider_requests": 0,
    "sdk_denials": [],
    "network_denials": [],
    "protected_path_denials": [],
    "browser_requests": [],
    "external_browser_requests": [],
    "browser_errors": [],
    "console_errors": [],
    "screenshots": [],
    "downloads": {},
    "scope": "actual candidate React UI/API/RealResearchService/LangGraph/SQLite/renderer; external model double ONLY",
    "counter_warning": "API provider=openai and actual_model_requests are injected callback bookkeeping, NOT actual provider traffic. Real shared ledger is never opened.",
    "unverified": [
        "실제 계정·인증·장부",
        "Luna/실제 제공자 수락",
        "역사적 진실",
        "의미 검토 모델의 판별 능력",
        "실제 웹 수집",
    ],
    "output": str(OUT),
    "data": str(DATA),
    "command": [sys.executable, str(Path(__file__).resolve())],
}
PAIR_SCOPE = threading.local()
ORIGINAL_PAIR = socket.socketpair


def stdlib_pair(*args, **kwargs):
    # Windows asyncio self-pipe만 원래 stdlib socketpair의 임시 loopback을 허용한다.
    PAIR_SCOPE.active = True
    try:
        return ORIGINAL_PAIR(*args, **kwargs)
    finally:
        PAIR_SCOPE.active = False


socket.socketpair = stdlib_pair


def audit(event, args):
    if (
        event in {"open", "sqlite3.connect"}
        and args
        and isinstance(args[0], (str, bytes, os.PathLike))
    ):
        path = os.fsdecode(args[0]).replace("\\", "/").lower()
        parts = path.split("/")
        if (
            "03_실행데이터" in parts
            or any(p == ".env" or p.startswith(".env.") for p in parts)
            or any(p in {".ssh", ".aws", ".azure"} for p in parts)
        ):
            PROOF["protected_path_denials"].append({"event": event, "path": path})
            raise AssertionError("protected_credentials_or_real_data_forbidden")
    if event in {"socket.connect", "socket.bind", "socket.sendto"}:
        address = args[-1] if event == "socket.sendto" else args[1]
        pair = getattr(PAIR_SCOPE, "active", False)
        if (
            not isinstance(address, tuple)
            or address[0] not in {"127.0.0.1", "::1"}
            or (address[1] != 8799 and not pair)
        ):
            PROOF["network_denials"].append(
                {
                    "event": event,
                    "address": repr(address),
                    "stack": traceback.format_stack(limit=10),
                }
            )
            raise AssertionError("only_8799_and_exact_stdlib_socketpair_allowed")
    if event == "socket.getaddrinfo" and args[0] not in {"127.0.0.1", "::1", "localhost", None}:
        PROOF["network_denials"].append({"event": event, "host": repr(args[0])})
        raise AssertionError("external_dns_forbidden")


sys.addaudithook(audit)


def check(name, ok, **facts):
    PROOF["checks"].append({"name": name, "passed": bool(ok), **facts})
    print(("PASS " if ok else "FAIL ") + name, flush=True)
    if not ok:
        raise AssertionError(name + ": " + repr(facts))


def sha(content):
    return hashlib.sha256(content).hexdigest()


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")


async def hit(locator):
    return await locator.evaluate(
        """el=>{const b=el.getBoundingClientRect(),x=b.x+b.width/2,y=b.y+b.height/2;return b.width>0&&b.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&el.contains(document.elementFromPoint(x,y));}"""
    )


async def capture(page, name):
    path = OUT / (name + ".png")
    await page.screenshot(path=str(path))
    PROOF["screenshots"].append(str(path))


async def execute():
    # urllib3의 import-time IPv6 능력 검사는 ::1:0 bind를 시도한다.
    # 허용 소켓 범위를 넓히지 않고, 이 외부통신 없는 하네스에서는 그 탐침만 생략한다.
    original_ipv6 = socket.has_ipv6
    socket.has_ipv6 = False
    try:
        import urllib3  # noqa: F401 - 좁은 감사 경계 안에서 import-time IPv6 탐침만 먼저 처리한다.
    finally:
        socket.has_ipv6 = original_ipv6
    PROOF["urllib3_import_ipv6_probe_suppressed"] = True
    import pypdfium2 as pdfium
    import uvicorn
    from playwright.async_api import async_playwright

    from 서버 import 실제모델, 호출예산
    from 서버.보고서근거 import DRAFT_PROTOCOL
    from 서버.실제연구제어 import RealResearchService
    from 서버.앱 import create_app
    from 테스트.테스트_실제연구제어 import CORPUS, ScriptedModel

    def sentinel(*args, **kwargs):
        PROOF["sdk_denials"].append("real_model_key_budget_or_sdk_constructor")
        raise AssertionError("real_provider_auth_ledger_forbidden")

    실제모델.OpenAIModel.__init__ = sentinel
    실제모델._project_key = sentinel
    호출예산.CallBudget.__init__ = sentinel
    실제모델.AsyncOpenAI.__init__ = sentinel

    class VisualScriptedModel(ScriptedModel):
        async def complete(self, *args, **kwargs):
            check_budget = len(self.calls) < 40
            if not check_budget:
                raise AssertionError("ephemeral_fake_budget_40_exceeded")
            response = await super().complete(*args, **kwargs)
            if kwargs["context"]["operation"] == "synthesize":
                # 실제 정상 초안 요청에 제공된 읽은 출처만 사용한다. 문서 이름 발명 금지.
                read_ids = []
                for _, user, context in self.calls:
                    if context["operation"] == "draft_section":
                        for evidence in json.loads(user)["evidence"]:
                            if evidence["source_id"] not in read_ids:
                                read_ids.append(evidence["source_id"])
                if not read_ids:
                    raise AssertionError("synthesis_requires_actual_read_source_ids")
                source = read_ids[0]
                PROOF["synthetic_visual_source_ids"] = [source]
                response["content"] = json.dumps(
                    {
                        "머리말": "명시적 외부 모델 대역을 사용한 로컬 경로 검사입니다. 역사적 사실 검증이나 실제 모델의 연구 성공을 의미하지 않습니다.",
                        "맺음말": "반복된 대역 본문은 경로 검사용 채움글이며 실제 보고서 품질이나 의미적 진실의 증거가 아닙니다.",
                        "표": [
                            {
                                "제목": "합성 검사 표 · 실제 연구 결과 아님",
                                "열": ["검사 단계", "합성 표시"],
                                "행": [
                                    {"셀": ["읽기와 초안", "명시적 대역 경로"], "출처": [source]},
                                    {"셀": ["표와 도식", "로컬 표현 검사"], "출처": [source]},
                                ],
                            }
                        ],
                        "도식": [
                            {
                                "제목": "합성 검사 도식 · 실제 연구 결과 아님",
                                "설명": "입력에서 문서까지의 시험 흐름이며 사건의 역사적 인과 관계를 나타내지 않습니다.",
                                "출처": [source],
                                "코드": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 160"><title>합성 검사 흐름</title><desc>명시적 대역의 로컬 표현 검사</desc><rect x="10" y="15" width="580" height="130" fill="#eef4ff" stroke="#234567"/><text x="35" y="85" font-size="24" fill="#123456">입력 → 합성 표 → 보고서</text></svg>',
                            }
                        ],
                        "시각화생략사유": "",
                    },
                    ensure_ascii=False,
                )
            return response

    paths = (
        [Path(__file__).resolve(), CORPUS]
        + [
            I / p
            for p in (
                "서버/보고서근거.py",
                "서버/원문구간.py",
                "서버/검토문맥.py",
                "서버/실제연구제어.py",
                "서버/앱.py",
                "서버/보고서품질.py",
                "서버/보고서변환.py",
                "서버/보고서표현.py",
                "서버/보고서도식.py",
                "테스트/테스트_실제연구제어.py",
                "화면/src/상세보기.tsx",
                "화면/src/보고서표현.tsx",
            )
        ]
        + sorted(p for p in STATIC.rglob("*") if p.is_file())
    )
    PROOF["source_hashes_start"] = {str(p): sha(p.read_bytes()) for p in paths}
    PROOF["draft_protocol"] = DRAFT_PROTOCOL
    check(
        "sentence/paragraph native protocol ready",
        DRAFT_PROTOCOL == "sentence-cited-units-v5",
    )
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', (STATIC / "index.html").read_text("utf-8"))
    check(
        "candidate index binds actual module assets",
        bool(scripts)
        and all(
            name.startswith("/assets/") and (STATIC / name.lstrip("/")).is_file()
            for name in scripts
        ),
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8799))
    check("8799 unused before start", True)
    model = VisualScriptedModel()
    holder = {}

    def factory(root):
        holder["service"] = RealResearchService(root, model, CORPUS)
        return holder["service"]

    app = create_app(DATA, STATIC, service_factory=factory)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=8799, log_level="warning", timeout_graceful_shutdown=2
        )
    )
    task = asyncio.create_task(server.serve())
    browser = None
    page = None
    try:
        for _ in range(150):
            if server.started or task.done():
                break
            await asyncio.sleep(0.03)
        check("owned uvicorn ready", server.started)
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path=str(R / ".cache/playwright/chromium-1248/chrome-win64/chrome.exe"),
                headless=True,
                args=["--disable-background-networking", "--no-proxy-server"],
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                accept_downloads=True,
                service_workers="block",
            )

            async def guard(route):
                request = route.request
                address = urlsplit(request.url)
                if address.scheme in {"http", "https"} and not request.url.startswith(BASE + "/"):
                    PROOF["external_browser_requests"].append(request.url)
                    await route.abort()
                    return
                if request.method != "GET" and not (
                    request.method == "POST"
                    and (address.path == "/api/spike/commands" or address.path.endswith("/exports"))
                ):
                    PROOF["external_browser_requests"].append("unapproved mutation " + request.url)
                    await route.abort()
                    return
                PROOF["browser_requests"].append({"method": request.method, "url": request.url})
                await route.continue_()

            await context.route("**/*", guard)
            page = await context.new_page()
            page.set_default_timeout(15000)
            page.on("pageerror", lambda error: PROOF["browser_errors"].append(str(error)))

            def console_event(msg):
                if msg.type != "error":
                    return
                if (
                    msg.text
                    == "Blocked script execution in 'about:srcdoc' because the document's frame is sandboxed and the 'allow-scripts' permission is not set."
                ):
                    PROOF.setdefault("sandbox_security_diagnostics", []).append(msg.text)
                else:
                    PROOF["console_errors"].append(msg.text)

            page.on("console", console_event)

            async def get(path):
                response = await context.request.get(BASE + path)
                if response.status != 200:
                    raise AssertionError(f"GET {path}: {response.status}")
                return await response.json()

            await page.goto(BASE + "/#/control")
            await page.locator('[data-testid="world"][data-render-ready="true"]').wait_for()
            initial = await get("/api/spike/snapshot")
            check(
                "empty isolated RealResearchService local state",
                initial["provider"] == "openai"
                and initial["mode"] == "research"
                and initial["selected_source_mode"] == "local"
                and not initial["runs"]
                and not initial["queue"],
            )
            PROOF["injected_fake_budget_before"] = initial["budget"]
            await page.get_by_test_id("command-input").fill(
                "/디비 1894년에 일어난 주요 사건들은 무엇이고 각각의 원인은 무엇인가?"
            )
            async with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/api/spike/commands")
            ) as admission:
                await page.get_by_test_id("command-submit").click()
            accepted = await admission.value
            check("actual UI admission 202", accepted.status == 202)
            PROOF["admission"] = await accepted.json()
            deadline = time.monotonic() + 30
            snap = None
            while time.monotonic() < deadline:
                snap = await get("/api/spike/snapshot")
                if snap["runs"] and snap["runs"][0]["status"] != "running":
                    break
                await asyncio.sleep(0.1)
            run = snap["runs"][0]
            PROOF["run"] = run
            dump("API_완료.json", snap)
            archive = await get(f"/api/spike/runs/{run['run_id']}/archive")
            dump("아카이브.json", archive)
            check(
                "completed with static and explicit fake semantic pass",
                run["status"] == "completed"
                and run["quality"]["passed"]
                and run["quality"]["semantic_review"]["통과"] is True,
                quality=run.get("quality"),
                status=run["status"],
            )
            operations = [c[2]["operation"] for c in model.calls]
            PROOF["double_operations"] = dict(Counter(operations))
            PROOF["ephemeral_double_call_count"] = len(operations)
            PROOF["api_callback_counter_NOT_real_requests"] = run["actual_model_requests"]
            check(
                "all normal model operations and second round reached",
                set(operations)
                >= {"plan", "select", "read_one", "draft_section", "synthesize", "semantic_review"}
                and model.section_calls
                == {"researcher-1": 2, "researcher-2": 1, "researcher-3": 1, "researcher-4": 1}
                and len(operations) == run["actual_model_requests"] <= 40,
            )
            labels = {s["label"] for s in archive["spans"] if s["status"] == "completed"}
            check(
                "six graph nodes plus publication actually completed",
                labels
                >= {"plan", "dispatch", "research", "review", "synthesize", "evaluate", "publish"},
                completed_labels=sorted(labels),
            )
            check("one run and queue empty", len(snap["runs"]) == 1 and not snap["queue"])
            report = await get(f"/api/spike/reports/{run['report_id']}")
            PROOF["report_id"] = report["report_id"]
            PROOF["report_sha256"] = report["sha256"]
            dump("정본_API.json", report)
            with sqlite3.connect(DATA / "실행기록.sqlite3") as db:
                stored = json.loads(
                    db.execute(
                        "SELECT body FROM reports WHERE report_id=?", (report["report_id"],)
                    ).fetchone()[0]
                )
                rows = db.execute("SELECT input,output,status FROM calls ORDER BY rowid").fetchall()
                integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            check(
                "SQLite published canonical equals API and hash",
                stored == report
                and sha(report["markdown"].encode("utf-8")) == report["sha256"]
                and integrity == "ok",
            )
            journal = [
                {"input": json.loads(a), "output": json.loads(b), "status": c} for a, b, c in rows
            ]
            dump("모델대역_호출저널.json", journal)
            drafts = [r for r in journal if r["input"]["context"]["operation"] == "draft_section"]
            for number, row in enumerate(drafts, 1):
                payload, reply = row["input"], json.loads(row["output"]["content"])
                offered = json.loads(payload["user"])
                schema = payload["response_schema"]
                metadata = payload["inputs"]
                from 보고서품질_실제연구검사 import validate_draft_schema

                validate_draft_schema(schema, offered)
                valid_units, statement_number, seen_pairs = True, 0, set()
                offered_ids = {x["id"] for x in offered["evidence"]}
                for unit in reply["문장"]:
                    if type(unit) is not dict or set(unit) != {"내용", "근거구간"}:
                        valid_units = False
                        break
                    if type(unit["내용"]) is str:
                        if not unit["내용"].strip():
                            valid_units = False
                            break
                        statement_number += 1
                    elif unit["내용"] is not None or statement_number == 0:
                        valid_units = False
                        break
                    ref = unit["근거구간"]
                    if type(ref) is not int or ref not in offered_ids or (statement_number, ref) in seen_pairs:
                        valid_units = False
                        break
                    seen_pairs.add((statement_number, ref))
                check(
                    f"draft {number}: mandatory citation schema exact journal and offered binding",
                    set(reply)
                    == set(schema["required"])
                    == {"근거목록ID", "문장", "충분", "부족"}
                    and reply["근거목록ID"]
                    == offered["evidence_set_id"]
                    == schema["properties"]["근거목록ID"]["enum"][0]
                    and valid_units
                    and len(reply["문장"]) <= 16
                    and (not reply["충분"] or statement_number > 0)
                    and type(reply["충분"]) is bool
                    and type(reply["부족"]) is str
                    and metadata["draft_protocol"] == DRAFT_PROTOCOL
                    and metadata["response_schema"] == schema
                    and metadata["catalog"] == offered["evidence"]
                    and len(metadata["alias_map"])
                    == len(metadata["private_catalog"])
                    == len(offered["evidence"]),
                )
                check(
                    f"draft {number}: private hash scope and exact alias mapping",
                    all(
                        metadata["alias_map"][str(n)] == private[0]
                        and private[1:]
                        == [item["source_id"], item["start"], item["end"], item["text"]]
                        and private[0]
                        == "e"
                        + sha(
                            json.dumps(
                                [
                                    item["source_id"],
                                    metadata["source_hashes"][item["source_id"]],
                                    item["start"],
                                    item["end"],
                                ],
                                ensure_ascii=False,
                                allow_nan=False,
                            ).encode("utf-8")
                        )[:16]
                        for n, (private, item) in enumerate(
                            zip(metadata["private_catalog"], offered["evidence"]), 1
                        )
                    ),
                )
            observed = [s for s in model.schemas if s is not None]
            structured = [
                r
                for r in journal
                if r["input"]["context"]["operation"]
                in {"draft_section", "synthesize", "semantic_review"}
            ]
            check(
                "normal draft and review wrappers receive exact schemas without fallback",
                len(structured)
                == len(observed)
                == operations.count("draft_section")
                + operations.count("synthesize")
                + operations.count("semantic_review")
                and all(
                    row["input"]["response_schema"] == schema
                    for row, schema in zip(structured, observed)
                )
                and all(
                    (schema is not None)
                    == (call[2]["operation"] in {"draft_section", "synthesize", "semantic_review"})
                    for call, schema in zip(model.calls, model.schemas)
                ),
            )
            reviews = [
                r for r in structured if r["input"]["context"]["operation"] == "semantic_review"
            ]
            decision = json.loads(reviews[0]["output"]["content"]) if len(reviews) == 1 else {}
            check(
                "single contextual semantic call is request-bound and saved",
                len(reviews) == 1
                and decision.get("검토목록ID")
                == reviews[0]["input"]["response_schema"]["properties"]["검토목록ID"]["enum"][0]
                and decision.get("판정") == "supported"
                and decision.get("문제") == []
                and run["quality"]["semantic_review"].get("검토계약") == "source-context-review-v1",
            )
            PROOF["schemas_sha256"] = [
                sha(json.dumps(s, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                for s in observed
            ]
            await page.get_by_test_id("paper-document").wait_for()
            await capture(page, "완료_문서제출")
            await page.get_by_test_id("paper-document").click()
            await page.get_by_test_id("report-viewer").wait_for()
            frame = page.frame_locator("iframe.report-presentation-frame")
            await frame.locator("table").wait_for()
            await frame.locator("svg").wait_for()
            iframe = page.locator("iframe.report-presentation-frame")
            check(
                "actual sandboxed report frame table SVG and source captions",
                await iframe.get_attribute("sandbox") == ""
                and await frame.locator("table").count() >= 1
                and await frame.locator("svg").count() >= 1
                and await frame.locator("script,img").count() == 0
                and "합성 검사 표" in await frame.locator("body").inner_text()
                and "합성 검사 도식" in await frame.locator("body").inner_text()
                and PROOF["synthetic_visual_source_ids"][0]
                in await frame.locator("body").inner_text(),
            )
            for format in ("md", "html", "pdf"):
                if format == "md":
                    link = page.get_by_role("link", name="Markdown 다운로드", exact=True)
                else:
                    create = page.get_by_test_id(f"export-{format}-create")
                    if await create.count():
                        await create.click()
                    link = page.get_by_test_id(f"export-{format}-download")
                    await link.wait_for(timeout=45000)
                async with page.expect_download() as download:
                    await link.click()
                destination = OUT / (
                    "🧾 명시적대역_보고서.md" if format == "md" else "명시적대역_보고서." + format
                )
                await (await download.value).save_as(str(destination))
                content = destination.read_bytes()
                PROOF["downloads"][format] = {
                    "path": str(destination),
                    "sha256": sha(content),
                    "bytes": len(content),
                }
                if format == "md":
                    check(
                        "actual MD download reopened equals canonical bytes",
                        content.decode("utf-8") == report["markdown"]
                        and sha(content) == report["sha256"],
                    )
                elif format == "html":
                    opened = await context.new_page()
                    await opened.set_content(content.decode("utf-8"), wait_until="load")
                    check(
                        "actual HTML download reopened rendered table SVG Korean captions",
                        await opened.locator("table").count() >= 1
                        and await opened.locator("svg").count() >= 1
                        and "합성 검사 도식" in await opened.locator("body").inner_text(),
                    )
                    await opened.locator("svg").first.scroll_into_view_if_needed()
                    await capture(opened, "HTML_파일_재열기")
                    await opened.close()
                else:
                    with pdfium.PdfDocument(content) as pdf:
                        texts = []
                        for n in range(len(pdf)):
                            pdfpage = pdf[n]
                            textpage = pdfpage.get_textpage()
                            texts.append(textpage.get_text_range())
                            textpage.close()
                            pdfpage.close()
                        joined = "".join("".join(texts).split())
                        check(
                            "actual PDF reopened Korean table diagram caption source preserved",
                            all(
                                "".join(t.split()) in joined
                                for t in [
                                    report["title"],
                                    "합성 검사 표",
                                    "합성 검사 도식",
                                    PROOF["synthetic_visual_source_ids"][0],
                                ]
                            ),
                            pages=len(pdf),
                        )
                        PROOF["pdf_pages"] = len(pdf)
                        indices = list(
                            dict.fromkeys(
                                [
                                    0,
                                    next(
                                        (
                                            n
                                            for n, t in enumerate(texts)
                                            if "합성검사도식" in "".join(t.split())
                                        ),
                                        len(pdf) - 1,
                                    ),
                                ]
                            )
                        )
                        for n in indices:
                            pdfpage = pdf[n]
                            bitmap = pdfpage.render(scale=1.25)
                            target = OUT / f"PDF_페이지_{n + 1}.png"
                            bitmap.to_pil().save(target)
                            PROOF["screenshots"].append(str(target))
                            bitmap.close()
                            pdfpage.close()
                    dump("PDF_추출문자.json", texts)
                if format != "md":
                    exports = (await get(f"/api/spike/reports/{report['report_id']}/exports"))[
                        "exports"
                    ]
                    entry = next(x for x in exports if x["format"] == format)
                    check(
                        format + " actual export metadata source and file hash",
                        entry["sha256"] == sha(content)
                        and entry["source_report_hash"] == report["sha256"],
                    )
            for width, height in ((1440, 900), (390, 844), (844, 390)):
                await page.set_viewport_size({"width": width, "height": height})
                await asyncio.sleep(0.3)
                await iframe.evaluate(
                    """el=>{const root=el.closest('.popup-body');const a=el.getBoundingClientRect(),b=root.getBoundingClientRect();root.scrollTop+=a.top-b.top-4;}"""
                )
                label = frame.locator("svg text").first
                reached = False
                inside = next(f for f in page.frames if f != page.main_frame)
                await inside.evaluate("window.scrollTo(0,0)")
                for _ in range(65):
                    box, outer, clip = (
                        await label.bounding_box(),
                        await iframe.bounding_box(),
                        await page.locator(".popup-body").bounding_box(),
                    )
                    top = max(outer["y"], clip["y"])
                    bottom = min(outer["y"] + outer["height"], clip["y"] + clip["height"], height)
                    if box and box["y"] >= top and box["y"] + box["height"] <= bottom:
                        reached = True
                        break
                    await page.mouse.move(outer["x"] + outer["width"] / 2, (top + bottom) / 2)
                    delta = (
                        min(160, max(20, box["y"] + box["height"] - bottom + 8))
                        if box["y"] + box["height"] > bottom
                        else max(-160, min(-20, box["y"] - top - 8))
                    )
                    await page.mouse.wheel(0, delta)
                    await asyncio.sleep(0.06)
                if not reached:
                    await capture(page, f"실패_도식_{width}x{height}")
                check(
                    f"viewport {width}x{height} SVG label reachable no root xoverflow close hit",
                    reached
                    and await page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    and await hit(page.get_by_test_id("popup-close")),
                    svg_box=box,
                    iframe_box=outer,
                    popup_clip=clip,
                )
                await capture(page, f"도식_{width}x{height}")
                last = page.get_by_test_id("export-pdf-download")
                await last.scroll_into_view_if_needed()
                check(
                    f"viewport {width}x{height} last download control accessible", await hit(last)
                )
                check(
                    f"viewport {width}x{height} iframe header table SVG text retained",
                    bool(await frame.locator("h1").inner_text())
                    and await frame.locator("table").count() >= 1
                    and await label.text_content() == "입력 → 합성 표 → 보고서",
                )
            before_calls = len(model.calls)
            before_db = sha((DATA / "실행기록.sqlite3").read_bytes())
            await page.get_by_test_id("popup-close").click()
            await page.reload()
            await page.locator('[data-testid="world"][data-render-ready="true"]').wait_for()
            await page.get_by_test_id("paper-document").click()
            await page.get_by_test_id("report-viewer").wait_for()
            await page.frame_locator("iframe.report-presentation-frame").locator("table").wait_for()
            await get(f"/api/spike/reports/{report['report_id']}")
            presentation_url = next(
                r["url"]
                for r in reversed(PROOF["browser_requests"])
                if f"/api/spike/reports/{report['report_id']}/presentation?" in r["url"]
            )
            presentation = await context.request.get(presentation_url)
            check(
                "presentation GET succeeds with actual UI binding parameters",
                presentation.status == 200,
                status=presentation.status,
                url=presentation_url,
            )
            await get(f"/api/spike/reports/{report['report_id']}/exports")
            final = await get("/api/spike/snapshot")
            admissions = [
                r
                for r in PROOF["browser_requests"]
                if r["method"] == "POST" and r["url"].endswith("/api/spike/commands")
            ]
            check(
                "reload reopen GETs no new model or run queue DB writes",
                len(model.calls) == before_calls
                and len(final["runs"]) == 1
                and not final["queue"]
                and len(admissions) == 1
                and before_db == sha((DATA / "실행기록.sqlite3").read_bytes()),
            )
            check(
                "no external traffic credential SDK attempts or page errors",
                not any(
                    PROOF[k]
                    for k in (
                        "network_denials",
                        "sdk_denials",
                        "protected_path_denials",
                        "external_browser_requests",
                        "browser_errors",
                        "console_errors",
                    )
                ),
            )
            PROOF["injected_fake_budget_after"] = final["budget"]
            PROOF["passed"] = True
            await browser.close()
            browser = None
    except BaseException:
        if page is not None and not page.is_closed():
            try:
                await capture(page, "실패_화면")
            except Exception as capture_error:  # noqa: BLE001 - 원 실패를 덮지 않는 진단 경계
                PROOF["failure_capture_error"] = type(capture_error).__name__
        raise
    finally:
        if browser is not None:
            await browser.close()
        server.should_exit = True
        await asyncio.wait_for(task, timeout=15)
        PROOF["model_closed"] = model.closed
        PROOF["server_task_done"] = task.done()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 8799))
        PROOF["port_8799_closed"] = True
        PROOF["source_hashes_end"] = {str(p): sha(p.read_bytes()) for p in paths}
        if PROOF["source_hashes_start"] != PROOF["source_hashes_end"]:
            PROOF["passed"] = False
            PROOF["source_changed_during_run"] = True
        check(
            "closed model owned server port and unchanged sources",
            model.closed
            and task.done()
            and PROOF["source_hashes_start"] == PROOF["source_hashes_end"],
        )


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    DATA.mkdir(parents=True, exist_ok=False)
    scratch = DATA / "임시"
    scratch.mkdir()
    for name in ("TMPDIR", "TEMP", "TMP"):
        os.environ[name] = str(scratch)
    import tempfile

    tempfile.tempdir = str(scratch)
    PROOF["runtime_temp_dir"] = str(scratch)
    started = time.monotonic()
    try:
        asyncio.run(asyncio.wait_for(execute(), timeout=170))
    except BaseException as error:  # noqa: BLE001 - 종료/실패 원본을 검증 증거에 보존하는 최외곽
        PROOF["passed"] = False
        PROOF["error"] = str(error)
        PROOF["traceback"] = traceback.format_exc()
    finally:
        PROOF["elapsed_seconds"] = round(time.monotonic() - started, 3)
        PROOF["checks_passed"] = sum(c["passed"] for c in PROOF["checks"])
        PROOF["checks_count"] = len(PROOF["checks"])
        PROOF["artifact_manifest"] = {
            str(p): {"bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
            for p in sorted(OUT.iterdir())
            if p.is_file()
        }
        dump("확인.json", PROOF)
        print(
            json.dumps(
                {
                    "passed": PROOF["passed"],
                    "checks": PROOF["checks_count"],
                    "evidence": str(OUT / "확인.json"),
                    "error": PROOF.get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0 if PROOF["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
