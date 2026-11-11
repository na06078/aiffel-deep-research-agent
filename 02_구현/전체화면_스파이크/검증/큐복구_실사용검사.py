"""격리 FakeModel의 실제 Chromium → HTTP → SQLite 큐 복구 검사.

실제 제공자 모듈/키/장부를 사용하지 않는다. Python 감사와 브라우저 라우팅은
프로세스 내부 방어이며 OS 샌드박스가 아니다. 기존 관계목록 검사 패턴 재사용.
"""

import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

I = Path(__file__).resolve().parents[1]
R = I.parents[1]
sys.path.insert(0, str(I))
sys.dont_write_bytecode = True
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"
LABEL = uuid.uuid4().hex
OUT = I / "검증결과/제출/큐실사용" / LABEL
DATA = R / ".cache/보고서품질_검증/실모델점검/제출_큐실사용" / LABEL
STATIC = R / ".cache/제출_화면"
PROOF: dict[str, Any] = {
    "passed": False,
    "run_label": LABEL,
    "checks": [],
    "requests": [],
    "responses": [],
    "network_denials": [],
    "protected_path_denials": [],
    "external_browser_requests": [],
    "browser_errors": [],
    "console_errors": [],
    "screenshots": [],
    "actual_provider_requests": 0,
    "explicit_model_double": True,
    "provider": "test-double",
    "output": str(OUT),
    "data": str(DATA),
    "limitations": [
        "FakeModel only; not real provider acceptance",
        "Process guards are not an OS sandbox",
        "Native Korean IME and human visual review not tested",
    ],
    "command": [sys.executable, str(Path(__file__).resolve())],
}
PAIR_SCOPE = threading.local()
ORIGINAL_PAIR = socket.socketpair


def stdlib_pair(*args, **kwargs):
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
        if "03_실행데이터" in parts or any(
            x == ".env" or x.startswith(".env.") or x in {".ssh", ".aws", ".azure"} for x in parts
        ):
            PROOF["protected_path_denials"].append(path)
            raise AssertionError("protected_path_forbidden")
    if event == "import" and args[0] in {"서버.실제모델", "서버.호출예산", "openai"}:
        raise AssertionError("real_provider_module_forbidden: " + args[0])
    if event in {"socket.connect", "socket.bind", "socket.sendto"}:
        address = args[-1] if event == "socket.sendto" else args[1]
        if (
            not isinstance(address, tuple)
            or address[0] not in {"127.0.0.1", "::1"}
            or (address[1] != PORT and not getattr(PAIR_SCOPE, "active", False))
        ):
            PROOF["network_denials"].append({"event": event, "address": repr(address)})
            raise AssertionError("only_owned_loopback_and_stdlib_socketpair_allowed")
    if event == "socket.getaddrinfo" and args[0] not in {"127.0.0.1", "::1", "localhost", None}:
        PROOF["network_denials"].append({"event": event, "host": repr(args[0])})
        raise AssertionError("external_dns_forbidden")


sys.addaudithook(audit)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")


def check(name, ok, **facts):
    PROOF["checks"].append(dict(name=name, passed=bool(ok), **facts))
    print(("PASS " if ok else "FAIL ") + name, flush=True)
    if not ok:
        raise AssertionError(name + ": " + repr(facts))


async def hit(locator):
    return await locator.evaluate(
        """el=>{const b=el.getBoundingClientRect(),x=b.x+b.width/2,y=b.y+b.height/2;return b.width>0&&b.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&el.contains(document.elementFromPoint(x,y));}"""
    )


async def capture(page, name):
    path = OUT / (name + ".png")
    await page.screenshot(path=str(path))
    PROOF["screenshots"].append(str(path))


def database_record(run_id):
    with sqlite3.connect(f"{(DATA / '실행기록.sqlite3').as_uri()}?mode=ro", uri=True) as db:
        revisions = db.execute(
            "SELECT revision,body FROM revisions WHERE run_id=? ORDER BY revision", (run_id,)
        ).fetchall()
    return {
        "revisions": revisions,
        "sha256": sha(json.dumps(revisions, ensure_ascii=False).encode()),
    }


async def execute():
    original_ipv6 = socket.has_ipv6
    socket.has_ipv6 = False
    try:
        import urllib3  # noqa: F401 - prevent unrelated import-time IPv6 port-0 probe
    finally:
        socket.has_ipv6 = original_ipv6
    import uvicorn
    from playwright.async_api import async_playwright

    from 서버.모델대역 import FakeModel
    from 서버.실행제어 import ExecutionService
    from 서버.앱 import create_app

    paths = (
        [Path(__file__).resolve()]
        + [
            I / f
            for f in [
                "서버/앱.py",
                "서버/실행제어.py",
                "서버/저장소.py",
                "서버/모델대역.py",
                "화면/src/앱.tsx",
                "화면/src/통신.ts",
                "화면/src/큐복구.tsx",
                "화면/src/계약.ts",
            ]
        ]
        + sorted(x for x in STATIC.rglob("*") if x.is_file())
    )
    PROOF["source_hashes_start"] = {str(x): sha(x.read_bytes()) for x in paths}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    app = create_app(data_dir=DATA, static_dir=STATIC)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=PORT, log_level="warning", timeout_graceful_shutdown=2
        )
    )
    task = asyncio.create_task(server.serve())
    browser = page = None
    try:
        for _ in range(150):
            if server.started or task.done():
                break
            await asyncio.sleep(0.03)
        check("owned server ready", server.started)
        service = app.state.service
        check(
            "default ExecutionService and explicit FakeModel only",
            type(service) is ExecutionService and type(service.model) is FakeModel,
        )
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(
                executable_path=str(R / ".cache/playwright/chromium-1248/chrome-win64/chrome.exe"),
                headless=True,
                args=["--no-proxy-server", "--disable-background-networking"],
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900}, service_workers="block"
            )
            allowed_posts = {"/api/spike/commands", "/api/spike/releases", "/api/spike/cancel"}

            async def guard(route):
                req = route.request
                url = urlsplit(req.url)
                approved = req.url.startswith(BASE + "/") and (
                    req.method == "GET"
                    or req.method == "POST"
                    and (
                        url.path in allowed_posts
                        or url.path.endswith("/retry")
                        or url.path.startswith("/api/spike/queue/")
                        and url.path.endswith("/cancel")
                    )
                )
                if not approved:
                    PROOF["external_browser_requests"].append(req.url)
                    await route.abort()
                    return
                PROOF["requests"].append(
                    {
                        "method": req.method,
                        "path": url.path,
                        "body": req.post_data_json if req.method == "POST" else None,
                    }
                )
                await route.continue_()

            await context.route("**/*", guard)
            page = await context.new_page()
            page.set_default_timeout(12000)
            page.on("pageerror", lambda e: PROOF["browser_errors"].append(str(e)))
            page.on(
                "console",
                lambda m: PROOF["console_errors"].append(m.text) if m.type == "error" else None,
            )

            async def get():
                response = await context.request.get(BASE + "/api/spike/snapshot")
                assert response.status == 200
                return await response.json()

            async def wait_state(predicate):
                for _ in range(150):
                    state = await get()
                    if predicate(state):
                        return state
                    await asyncio.sleep(0.05)
                raise AssertionError("snapshot wait expired: " + repr(state))

            async def post_click(locator, suffix, expected):
                async with page.expect_response(
                    lambda r: r.request.method == "POST" and urlsplit(r.url).path.endswith(suffix)
                ) as pending:
                    await locator.click()
                response = await pending.value
                body = await response.json()
                PROOF["responses"].append(
                    {"path": urlsplit(response.url).path, "status": response.status, "body": body}
                )
                check("UI HTTP " + suffix, response.status == expected, body=body)
                return body

            async def popup(name):
                if await page.get_by_test_id("popup-close").count():
                    await page.get_by_test_id("popup-close").click()
                if not await page.get_by_test_id("drawer").count():
                    await page.get_by_test_id("menu-toggle").click()
                await (
                    page.get_by_test_id("drawer")
                    .get_by_role("button", name=name, exact=True)
                    .click()
                )
                await page.get_by_role("dialog").wait_for()

            await page.goto(BASE + "/#/control")
            await page.locator('[data-testid="world"][data-render-ready="true"]').wait_for()
            state = await get()
            check(
                "isolated empty SQLite fixture",
                state["provider"] == "test-double" and not state["runs"] and not state["queue"],
            )
            admissions = []
            for text in [
                "격리 큐 복구 원 질문",
                "/큐 첫 번째 취소할 지시",
                "/큐 두 번째 보존할 지시",
            ]:
                await page.get_by_test_id("command-input").fill(text)
                admissions.append(
                    await post_click(page.get_by_test_id("command-submit"), "/commands", 202)
                )
            PROOF["fixture_admissions"] = admissions
            original_id = admissions[0]["run_id"]
            first_id, second_id = [a["instruction_id"] for a in admissions[1:]]
            await wait_state(lambda s: len(s["waiting"]) == 4 and len(s["queue"]) == 2)
            draft = "보존되어야 하는 미전송 입력 — 큐 복구"
            await page.get_by_test_id("command-input").fill(draft)
            await popup("제어 도구")
            await post_click(
                page.get_by_role("button", name="실패 응답", exact=True), "/releases", 200
            )
            failed = await wait_state(lambda s: s["runs"][0]["status"] == "failed")
            # Complete stale gates so the original failed graph cannot still mutate its journal.
            await page.get_by_role("button", name="이전 응답 돌려주기", exact=True).wait_for()
            if failed["waiting"]:
                await post_click(
                    page.get_by_role("button", name="이전 응답 돌려주기", exact=True),
                    "/releases",
                    200,
                )
            await wait_state(lambda s: not s["waiting"])
            await asyncio.sleep(0.2)
            failed = await get()
            dump("01_실패_정본.json", failed)
            original = failed["runs"][0]
            old_record = database_record(original_id)
            PROOF["old_record_before"] = old_record
            check(
                "failure pauses two queued instructions",
                original["status"] == "failed"
                and failed["queue_paused"]
                and len(failed["queue"]) == 2,
            )
            await popup("실행/큐")
            cancel_first = page.get_by_test_id("queue-cancel-" + first_id)
            await cancel_first.scroll_into_view_if_needed()
            await capture(page, "01_실패_큐2_1440")
            await post_click(cancel_first, "/cancel", 200)
            cancelled = await wait_state(lambda s: len(s["queue"]) == 1)
            check(
                "first queued cancellation preserves other item failed run and pause",
                cancelled["queue"] == failed["queue"][1:]
                and cancelled["runs"] == failed["runs"]
                and cancelled["current_run_id"] == original_id
                and cancelled["queue_paused"] == failed["queue_paused"]
                and cancelled["queue_pause_reason"] == failed["queue_pause_reason"],
            )
            dump("02_첫대기취소_정본.json", cancelled)
            retry_button = page.get_by_test_id("run-retry")
            await retry_button.scroll_into_view_if_needed()
            retry_admission = await post_click(retry_button, "/retry", 202)
            retry_id = retry_admission["run_id"]
            PROOF["retry_run_id"] = retry_id
            retried = await wait_state(lambda s: len(s["runs"]) == 2 and len(s["waiting"]) == 4)
            new = next(r for r in retried["runs"] if r["run_id"] == retry_id)
            check(
                "retry creates one distinct same-question/source run preserving original and revision",
                retry_id != original_id
                and new["retry_of_run_id"] == original_id
                and new["question"] == original["question"]
                and new["source_mode"] == original["source_mode"]
                and retried["runs"][0] == original
                and database_record(original_id) == old_record,
            )
            check(
                "retry preserves queue order and paused policy",
                retried["queue"] == cancelled["queue"]
                and retried["queue_paused"]
                and retried["queue_pause_reason"] == cancelled["queue_pause_reason"],
            )
            dump("03_재시도_정본.json", retried)
            await retry_button.scroll_into_view_if_needed()
            check(
                "retry disabled after accepted new running run", not await retry_button.is_enabled()
            )
            await capture(page, "02_재시도_1440")
            await page.get_by_test_id("popup-close").click()
            check(
                "draft survives popup opens closes recovery mutations",
                await page.get_by_test_id("command-input").input_value() == draft,
            )
            before_posts = len([r for r in PROOF["requests"] if r["method"] == "POST"])
            before_db = sha((DATA / "실행기록.sqlite3").read_bytes())
            await page.reload()
            await page.locator('[data-testid="world"][data-render-ready="true"]').wait_for()
            await popup("실행/큐")
            await page.get_by_test_id("queue-cancel-" + second_id).wait_for()
            restored = await get()
            check(
                "reload restores identical run queue pause without POST model or SQLite writes",
                restored == retried
                and before_posts == len([r for r in PROOF["requests"] if r["method"] == "POST"])
                and before_db == sha((DATA / "실행기록.sqlite3").read_bytes()),
            )
            dump("04_새로고침_정본.json", restored)
            await page.set_viewport_size({"width": 390, "height": 844})
            await asyncio.sleep(0.4)
            await page.get_by_test_id("command-input").fill(draft)
            await popup("실행/큐")
            last = page.get_by_role("dialog").get_by_role("button").last
            await last.scroll_into_view_if_needed()
            check(
                "390x844 last popup control and close actual hit tests no root overflow",
                await hit(last)
                and await hit(page.get_by_test_id("popup-close"))
                and await page.evaluate("document.documentElement.scrollWidth<=innerWidth"),
                last_label=await last.inner_text(),
            )
            await capture(page, "03_큐팝업_마지막조작_390")
            cancel_second = page.get_by_test_id("queue-cancel-" + second_id)
            await cancel_second.scroll_into_view_if_needed()
            check("390 queued cancel actual hit test", await hit(cancel_second))
            await capture(page, "04_대기취소_390")
            await post_click(cancel_second, "/cancel", 200)
            emptied = await wait_state(lambda s: not s["queue"])
            check(
                "second queued cancellation preserves both runs and pause",
                emptied["runs"] == restored["runs"]
                and emptied["queue_paused"] == restored["queue_paused"]
                and emptied["queue_pause_reason"] == restored["queue_pause_reason"],
            )
            await post_click(
                page.get_by_role("button", name="현재 실행 취소", exact=True), "/cancel", 200
            )
            final = await wait_state(lambda s: s["runs"][-1]["status"] == "cancelled")
            check(
                "owned retry cancelled via UI no queue starts old record preserved",
                len(final["runs"]) == 2
                and not final["queue"]
                and database_record(original_id) == old_record
                and final["runs"][0] == original,
            )
            await page.get_by_test_id("popup-close").click()
            check(
                "mobile draft survives popup and cancellation",
                await page.get_by_test_id("command-input").input_value() == draft,
            )
            await capture(page, "05_정리완료_390")
            dump("05_최종_정본.json", final)
            PROOF["old_record_after"] = database_record(original_id)
            with sqlite3.connect(f"{(DATA / '실행기록.sqlite3').as_uri()}?mode=ro", uri=True) as db:
                requests = [
                    {"request_id": a, "body": json.loads(b), "response": json.loads(c)}
                    for a, b, c in db.execute("SELECT request_id,body,response FROM requests")
                ]
                events = [
                    json.loads(x[0]) for x in db.execute("SELECT body FROM events ORDER BY seq")
                ]
                integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            dump("SQLite_요청.json", requests)
            dump("SQLite_사건.json", events)
            check(
                "SQLite integrity one retry acceptance and two queued cancel events",
                integrity == "ok"
                and sum(x["type"] == "run.retried" for x in events) == 1
                and sum(x["type"] == "instruction.cancelled" for x in events) == 2
                and sum(x["body"]["action"] == "retry_run" for x in requests) == 1,
            )
            PROOF["request_counts"] = dict(
                Counter(r["method"] + " " + r["path"] for r in PROOF["requests"])
            )
            check(
                "zero actual model traffic no real provider modules imported or browser errors",
                final["actual_model_requests"] == 0
                and all(r["actual_model_requests"] == 0 for r in final["runs"])
                and not any(m in sys.modules for m in ["서버.실제모델", "서버.호출예산", "openai"])
                and not any(
                    PROOF[k]
                    for k in [
                        "network_denials",
                        "protected_path_denials",
                        "external_browser_requests",
                        "browser_errors",
                        "console_errors",
                    ]
                ),
            )
            PROOF["passed"] = True
            await browser.close()
            browser = None
    except BaseException:
        if page is not None and not page.is_closed():
            try:
                await capture(page, "실패_화면")
                dump("실패_DOM.json", await page.locator("body").inner_text())
            except Exception as capture_error:  # noqa: BLE001 - preserve the original failure
                PROOF["failure_capture_error"] = repr(capture_error)
        raise
    finally:
        if browser is not None:
            await browser.close()
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        PROOF["source_hashes_end"] = {str(x): sha(x.read_bytes()) for x in paths}
        PROOF["server_task_done"] = task.done()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", PORT))
        PROOF["owned_port_closed"] = True
        check(
            "owned server closed and source/bundle identity unchanged",
            task.done() and PROOF["source_hashes_start"] == PROOF["source_hashes_end"],
        )


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    DATA.mkdir(parents=True, exist_ok=False)
    scratch = DATA / "임시"
    scratch.mkdir()
    for key in ("TMPDIR", "TEMP", "TMP"):
        os.environ[key] = str(scratch)
    import tempfile

    tempfile.tempdir = str(scratch)
    start = time.monotonic()
    try:
        asyncio.run(asyncio.wait_for(execute(), 180))
    except BaseException as error:  # noqa: BLE001 - outermost evidence and shutdown boundary
        PROOF["passed"] = False
        PROOF["error"] = str(error)
        PROOF["traceback"] = traceback.format_exc()
    finally:
        PROOF["elapsed_seconds"] = round(time.monotonic() - start, 3)
        PROOF["checks_count"] = len(PROOF["checks"])
        PROOF["checks_passed"] = sum(x["passed"] for x in PROOF["checks"])
        PROOF["owned_created_files"] = [str(Path(__file__).resolve())] + [
            str(x) for root in [OUT, DATA] for x in root.rglob("*") if x.is_file()
        ]
        PROOF["owned_created_files_count_before_manifest"] = len(PROOF["owned_created_files"])
        PROOF["artifact_manifest"] = {
            str(x): {"bytes": x.stat().st_size, "sha256": sha(x.read_bytes())}
            for x in OUT.rglob("*")
            if x.is_file()
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
