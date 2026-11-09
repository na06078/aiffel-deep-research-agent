"""후보 UI→API→실제 연구 그래프→격리 SQLite. 외부 모델은 명시적 대역만 사용."""

import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import sys
import time
import traceback
import uuid
from pathlib import Path

I = Path(__file__).resolve().parents[1]
R = I.parents[1]
sys.path.insert(0, str(I))
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import uvicorn
from playwright.async_api import async_playwright

from 서버.실제연구제어 import RealResearchService
from 서버.앱 import create_app
from 테스트.테스트_실제연구제어 import CORPUS, ScriptedModel

BASE = "http://127.0.0.1:8796"
OUT = I / "검증결과/보고서품질/차단화면" / uuid.uuid4().hex
DATA = R / ".cache/보고서품질_검증/차단화면" / OUT.name
STATIC = R / ".cache/보고서품질_화면"
MARKUP = '<img src=x onerror="window.__semanticInjected=1">'
EXCERPT = "자료에 근거한 연구 대역 문장으로 사실 검증 완료를 의미하지 않는다."
PROOF = {
    "explicit_model_double": True,
    "actual_provider_requests": 0,
    "unverified": ["실제 모델 판별 능력", "실제 제공자/인증/공유 예산", "실제 웹 수집"],
    "checks": [],
    "cases": [],
    "network_denials": [],
    "browser_errors": [],
    "console_errors": [],
    "external_browser_requests": [],
    "screenshots": [],
}


def audit(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
            PROOF["network_denials"].append(repr(address))
            raise AssertionError("external_socket_forbidden")
    elif event == "socket.getaddrinfo" and args[0] not in ("127.0.0.1", "::1", "localhost", None):
        PROOF["network_denials"].append(repr(args[0]))
        raise AssertionError("external_dns_forbidden")


def check(name, ok, **details):
    PROOF["checks"].append({"name": name, "passed": bool(ok), **details})
    if not ok:
        raise AssertionError(name + ": " + repr(details))


class BlockingModel(ScriptedModel):
    def __init__(self, verdict):
        super().__init__()
        self.verdict = verdict

    async def complete(self, *args, **kwargs):
        response = await super().complete(*args, **kwargs)
        if kwargs["context"]["operation"] == "semantic_review":
            response["content"] = (
                json.dumps(
                    {
                        "통과": False,
                        "문제": [
                            {
                                "유형": "근거 불일치 대역",
                                "설명": "명시적 대역 판정 " + MARKUP,
                                "본문발췌": EXCERPT,
                                "출처": [self.titles[0]],
                            }
                        ],
                        "검토요약": "의미 판별 성능을 입증하지 않는 차단 표시 검사",
                    },
                    ensure_ascii=False,
                )
                if self.verdict == "false"
                else "THIS IS NOT JSON <script>window.__semanticInjected=1</script>"
            )
        return response


async def panel(page, name):
    close = page.get_by_test_id("popup-close")
    if await close.is_visible():
        await close.click()
    drawer = page.get_by_test_id("drawer")
    if not await drawer.is_visible():
        await page.get_by_test_id("menu-toggle").click()
    await drawer.get_by_role("button", name=name, exact=True).click()
    await page.get_by_test_id("popup").wait_for()


async def hit(locator):
    return await locator.evaluate("""el => {
      const b=el.getBoundingClientRect(), x=b.x+b.width/2,y=b.y+b.height/2;
      return b.width>0 && b.height>0 && x>=0 && y>=0 && x<innerWidth && y<innerHeight
        && el.contains(document.elementFromPoint(x,y));
    }""")


async def capture(page, name):
    path = OUT / (name + ".png")
    await page.screenshot(path=str(path))
    PROOF["screenshots"].append(str(path))


async def case(driver, verdict):
    start = time.monotonic()
    model = BlockingModel(verdict)
    data = DATA / verdict
    holder = {}

    def factory(root):
        holder["service"] = RealResearchService(root, model, CORPUS)
        return holder["service"]

    app = create_app(data, STATIC, service_factory=factory)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=8796, log_level="warning", timeout_graceful_shutdown=2
        )
    )
    task = asyncio.create_task(server.serve())
    browser = None
    record = {"case": verdict, "data_dir": str(data)}
    PROOF["cases"].append(record)
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.03)
        check(verdict + ": server ready", server.started)
        browser = await driver.chromium.launch(
            executable_path=str(R / ".cache/playwright/chromium-1248/chrome-win64/chrome.exe"),
            headless=True,
            args=["--disable-background-networking", "--no-proxy-server"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900}, service_workers="block"
        )

        async def route_gate(route):
            url = route.request.url
            if url.startswith(("http:", "https:")) and not url.startswith(BASE + "/"):
                PROOF["external_browser_requests"].append(url)
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", route_gate)
        page = await context.new_page()
        page.set_default_timeout(5000)
        page.on("pageerror", lambda error: PROOF["browser_errors"].append(str(error)))
        page.on(
            "console",
            lambda msg: PROOF["console_errors"].append(msg.text) if msg.type == "error" else None,
        )
        await page.goto(BASE + "/#/control")
        await page.locator('[data-testid="world"][data-render-ready="true"]').wait_for(
            timeout=12000
        )
        await page.get_by_test_id("command-input").fill(
            "/디비 1894년에 일어난 주요 사건들은 무엇이고 각각의 원인은 무엇인가?"
        )
        async with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/api/spike/commands")
        ) as accepted:
            await page.get_by_test_id("command-submit").click()
        check(verdict + ": UI admission", (await accepted.value).status == 202)
        snap = None
        for _ in range(100):
            snap = await (await context.request.get(BASE + "/api/spike/snapshot")).json()
            if snap["runs"] and snap["runs"][0]["status"] != "running":
                break
            await asyncio.sleep(0.05)
        run = snap["runs"][0]
        record["run"] = run
        record["snapshot_queue_paused"] = snap["queue_paused"]
        reason = "semantic_review_failed" if verdict == "false" else "semantic_review_invalid"
        operations = [c[2]["operation"] for c in model.calls]
        record["double_operations"] = operations
        check(
            verdict + ": upstream reached semantic guard",
            all(
                op in operations
                for op in ["plan", "read_one", "draft_section", "synthesize", "semantic_review"]
            )
            and operations.count("semantic_review") == 1,
            operations=operations,
        )
        check(
            verdict + ": exact API block",
            run["status"] == "blocked"
            and run["phase"] == "quality_failed"
            and run["quality"]["reasons"] == [reason],
            quality=run["quality"],
        )
        check(
            verdict + ": draft preserved / no final / queue paused",
            bool(run["draft"]) and run["report_id"] is None and snap["queue_paused"],
        )
        archive = await (
            await context.request.get(BASE + f"/api/spike/runs/{run['run_id']}/archive")
        ).json()
        (OUT / f"{verdict}_api.json").write_text(
            json.dumps({"snapshot": snap, "archive": archive}, ensure_ascii=False, indent=2),
            "utf-8",
        )
        with sqlite3.connect(data / "실행기록.sqlite3") as db:
            stored = json.loads(
                db.execute(
                    "SELECT body FROM revisions WHERE run_id=? ORDER BY revision DESC LIMIT 1",
                    (run["run_id"],),
                ).fetchone()[0]
            )
            reports = db.execute("SELECT count(*) FROM reports").fetchone()[0]
            record["sqlite"] = {
                "integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0],
                "reports": reports,
                "revision_matches_api": stored == run,
            }
        check(
            verdict + ": SQLite durable draft and no report",
            stored["draft"] == run["draft"]
            and stored["quality"] == run["quality"]
            and reports == 0
            and record["sqlite"]["integrity_check"] == "ok",
        )
        expected = (
            "모델의 근거 검토에서 보완이 필요"
            if verdict == "false"
            else "검토 응답을 확인할 수 없어"
        )
        for width, height in [(1440, 900), (390, 844), (844, 390)]:
            await page.set_viewport_size({"width": width, "height": height})
            await panel(page, "실행/큐")
            notice = page.get_by_test_id("run-quality-blocked")
            await notice.wait_for()
            await notice.scroll_into_view_if_needed()
            text = await notice.inner_text()
            check(
                f"{verdict}/{width}: cause and next action",
                expected in text
                and all(
                    t in text
                    for t in [
                        "초안은 보존",
                        "자동 재시도하지 않습니다",
                        "과거 기록",
                        "새 연구에는 모델 호출이 다시 발생",
                    ]
                ),
            )
            if verdict == "false":
                check(
                    f"false/{width}: literal excerpt/source/markup",
                    EXCERPT in text
                    and model.titles[0] in text
                    and MARKUP in text
                    and await notice.locator("img,script").count() == 0
                    and await page.evaluate("window.__semanticInjected === undefined"),
                )
            check(
                f"{verdict}/{width}: one canvas and no root overflow",
                await page.locator("canvas").count() == 1
                and await page.evaluate("document.documentElement.scrollWidth <= innerWidth"),
            )
            check(
                f"{verdict}/{width}: close/input hit test",
                await hit(page.get_by_test_id("popup-close"))
                and await hit(page.get_by_test_id("command-input")),
            )
            await capture(page, f"{verdict}_{width}x{height}_실행큐")
            await page.get_by_test_id("popup-close").click()
            await page.get_by_test_id("command-input").fill("전송하지 않는 접근성 확인")
            check(
                f"{verdict}/{width}: input keyboard access",
                await page.get_by_test_id("command-input").evaluate(
                    "el=>el===document.activeElement"
                ),
            )
            await page.get_by_test_id("command-input").fill("")
        await page.set_viewport_size({"width": 1440, "height": 900})
        await panel(page, "과거 기록")
        await (
            page.get_by_test_id("popup")
            .get_by_role("button", name="자세히 보기", exact=True)
            .first.click()
        )
        notice = page.get_by_test_id("run-quality-blocked")
        await notice.wait_for()
        check(
            verdict + ": history explains cause and keeps draft",
            expected in await notice.inner_text()
            and run["draft"] in await page.get_by_test_id("popup").inner_text(),
        )
        await notice.scroll_into_view_if_needed()
        await capture(page, verdict + "_과거상세")
        before = len(model.calls)
        await page.wait_for_timeout(300)
        check(verdict + ": no automatic retry", len(model.calls) == before)
        record["result"] = "PASS"
    finally:
        if browser:
            await browser.close()
        server.should_exit = True
        await asyncio.wait_for(task, timeout=7)
        record["model_closed"] = model.closed
        record["elapsed_seconds"] = round(time.monotonic() - start, 3)
        check(verdict + ": service/model cleanup", model.closed)


async def main():
    OUT.mkdir(parents=True)
    DATA.mkdir(parents=True)
    PROOF["script"] = str(Path(__file__))
    PROOF["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    PROOF["candidate_assets"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (STATIC / "assets").glob("*.js")
    }
    PROOF["command"] = f'"{R / ".venv/Scripts/python.exe"}" "{Path(__file__)}"'
    with socket.socket() as probe:
        check("port unused before", probe.connect_ex(("127.0.0.1", 8796)) != 0)
    sys.addaudithook(audit)
    try:
        async with async_playwright() as driver:
            for verdict in ["false", "invalid"]:
                await asyncio.wait_for(case(driver, verdict), timeout=45)
        check(
            "no external request attempts",
            not PROOF["network_denials"] and not PROOF["external_browser_requests"],
        )
        check("no browser errors", not PROOF["browser_errors"] and not PROOF["console_errors"])
    except Exception:  # noqa: BLE001 - persist evidence for any validation failure
        PROOF["failure"] = traceback.format_exc()
    finally:
        with socket.socket() as probe:
            PROOF["listener_closed"] = probe.connect_ex(("127.0.0.1", 8796)) != 0
        PROOF["counts"] = {
            "checks": len(PROOF["checks"]),
            "passed": sum(c["passed"] for c in PROOF["checks"]),
            "cases": len(PROOF["cases"]),
            "screenshots": len(PROOF["screenshots"]),
        }
        PROOF["passed"] = "failure" not in PROOF and PROOF["listener_closed"]
        (OUT / "인계.json").write_text(json.dumps(PROOF, ensure_ascii=False, indent=2), "utf-8")
        print(
            json.dumps(
                {
                    "passed": PROOF["passed"],
                    "evidence": str(OUT),
                    "counts": PROOF["counts"],
                    "failure": PROOF.get("failure"),
                    "listener_closed": PROOF["listener_closed"],
                },
                ensure_ascii=False,
            )
        )
    return 0 if PROOF["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
