"""설치된 삭제 기능과 기존 보고서/재생을 GET만으로 검사한다. 실제 기록 삭제는 금지한다."""

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

import pypdfium2
from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")

from 화면개편_후보검사 import close_popup, close_timeline, hit_test, open_history_popup
from 화면도우미 import open_replay_from_history, return_to_live

BASE = "http://127.0.0.1:8768"
OUT = SPIKE / "검증결과/삭제/적용_설치본"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.load(response)


def fingerprint(snapshot):
    return {
        "runs": [
            (r["run_id"], r["status"], r["control_revision"], r["updated_at"])
            for r in snapshot["runs"]
        ],
        "current": snapshot["current_run_id"],
        "queue": snapshot["queue"],
        "queue_paused": snapshot["queue_paused"],
        "watermark": snapshot["high_watermark_seq"],
        "budget": snapshot["budget"],
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    snapshot = get("/api/spike/snapshot")
    before = fingerprint(snapshot)
    target = next(
        r for r in snapshot["runs"] if r.get("source_mode") == "web" and r.get("report_id")
    )
    blocked = next(r for r in snapshot["runs"] if r["status"] == "blocked")
    parent = next(
        r for r in snapshot["runs"] if r.get("report_id") == target.get("parent_report_id")
    )
    report = get(f"/api/spike/reports/{target['report_id']}")
    metadata = get(f"/api/spike/reports/{target['report_id']}/exports")
    checks, requests, errors, denied, screenshots = [], [], [], [], []
    downloads = ROOT / ".cache/삭제_설치다운로드"
    downloads.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, downloads_path=str(downloads))
        try:
            for width, height in [(1440, 900), (390, 844)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                page.set_default_timeout(15000)
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("request", lambda r: requests.append((r.method, r.url)))

                def guard(route):
                    request = route.request
                    if request.method != "GET" or not request.url.startswith(BASE + "/"):
                        denied.append((request.method, request.url))
                        route.abort()
                    else:
                        route.continue_()

                page.route("**/*", guard)
                page.goto(BASE + "/#/control")
                page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
                open_history_popup(page)
                page.get_by_test_id("history-item").nth(2).wait_for()
                assert page.get_by_test_id("history-item").count() == len(snapshot["runs"])

                def item(run, page=page):
                    return page.locator(
                        f'[data-testid="history-item"][data-run-id="{run["run_id"]}"]'
                    )

                item(blocked).get_by_test_id("history-delete").click()
                confirm = page.get_by_test_id("history-delete-confirm")
                page.get_by_test_id("history-delete-yes").wait_for()
                assert (
                    "28건" in confirm.inner_text() and "되돌릴 수 없습니다" in confirm.inner_text()
                )
                controls = [hit_test(button) for button in confirm.locator("button").all()]
                assert all(button["hit"] and button["inView"] for button in controls)
                file = OUT / f"삭제확인_{width}x{height}.png"
                page.screenshot(path=str(file))
                screenshots.append(str(file))
                page.get_by_test_id("history-delete-cancel").click()
                item(parent).get_by_test_id("history-delete").click()
                page.get_by_test_id("history-delete-blocked").wait_for()
                assert page.get_by_test_id("history-delete-yes").count() == 0
                page.get_by_test_id("history-delete-cancel").click()
                checks.append(
                    {
                        "name": f"삭제 진입·취소·부모 보호 {width}",
                        "passed": True,
                        "controls": controls,
                    }
                )

                item(target).get_by_role("button", name="자세히 보기", exact=True).click()
                page.get_by_role("button", name="최종 보고서 보기", exact=True).click()
                page.get_by_test_id("report-viewer").wait_for()
                page.get_by_test_id("export-pdf-download").wait_for()
                file = OUT / f"기존보고서_{width}x{height}.png"
                page.screenshot(path=str(file))
                screenshots.append(str(file))
                for format in ("md", "html", "pdf"):
                    link = (
                        page.get_by_role("link", name="Markdown 다운로드", exact=True)
                        if format == "md"
                        else page.get_by_test_id(f"export-{format}-download")
                    )
                    with page.expect_download() as download:
                        link.click()
                    content = Path(download.value.path()).read_bytes()
                    expected = (
                        report["sha256"]
                        if format == "md"
                        else next(
                            entry["sha256"]
                            for entry in metadata["exports"]
                            if entry["format"] == format
                        )
                    )
                    assert hashlib.sha256(content).hexdigest() == expected
                    pages = None
                    if format == "pdf":
                        with pypdfium2.PdfDocument(content) as document:
                            pages = len(document)
                            assert pages > 0
                    checks.append(
                        {
                            "name": f"기존 {format} 다운로드·재열기 {width}",
                            "passed": True,
                            "bytes": len(content),
                            "sha256": expected,
                            "pdf_pages": pages,
                        }
                    )
                close_popup(page)
                open_replay_from_history(page, target["run_id"])
                page.reload()
                page.wait_for_function(
                    '()=>document.querySelector("[data-testid=timeline-subject]")?.dataset.mode==="replay"'
                )
                assert (
                    page.get_by_test_id("timeline-subject").get_attribute("data-run-id")
                    == target["run_id"]
                )
                return_to_live(page)
                assert page.get_by_test_id("timeline-subject").get_attribute("data-mode") == "live"
                close_timeline(page)
                checks.append({"name": f"기록 재생·새로고침·실시간 복귀 {width}", "passed": True})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                context.close()
        finally:
            browser.close()
    after = fingerprint(get("/api/spike/snapshot"))
    result = {
        "checks": checks,
        "errors": errors,
        "denied": denied,
        "requests": requests,
        "before": before,
        "after": after,
        "unchanged": before == after,
        "new_model_requests": after["budget"]["attempt_count"] - before["budget"]["attempt_count"],
        "screenshots": screenshots,
    }
    (OUT / "확인.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert before == after and not errors and not denied
    print(
        json.dumps(
            {
                "checks_passed": len(checks),
                "errors": len(errors),
                "denied": len(denied),
                "unchanged": before == after,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
