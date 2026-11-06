"""실제 앱의 긴 접수 알림과 좁은 보고서 겹침 회귀. POST만 명시 대역이다."""

import json
import os
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
BASE = "http://127.0.0.1:8768"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def state():
    with urllib.request.urlopen(BASE + "/api/spike/snapshot", timeout=15) as response:
        return json.load(response)


def main():
    before = state()
    run = next(x for x in before["runs"] if x["run_id"] == before["current_run_id"])
    measurements = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(BASE + "/#/control")
        page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
        page.route(
            "**/api/spike/commands",
            lambda route: route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {
                        "instruction_id": "explicit-ui-test-double",
                        "run_id": run["run_id"],
                        "mode": "immediate",
                        "status": "received",
                        "control_revision": run["control_revision"],
                    }
                ),
            ),
        )
        page.get_by_test_id("command-input").fill("알림 겹침 검사 — 전송 대역")
        page.get_by_test_id("command-submit").click()
        page.get_by_test_id("paper-document").click()
        page.get_by_test_id("report-viewer").wait_for()
        for width, height in ((390, 844), (1440, 900), (844, 390)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(150)
            value = page.evaluate("""() => {const p=document.querySelector('.popup').getBoundingClientRect();
              const c=document.querySelector('.composer-area').getBoundingClientRect();
              const body=document.querySelector('.popup-body');body.scrollTop=body.scrollHeight;
              return {width:innerWidth,height:innerHeight,popupBottom:p.bottom,composerTop:c.top,
              popupScrollTop:body.scrollTop,popupScrollHeight:body.scrollHeight,windowScroll:scrollY};}""")
            measurements.append(value)
            assert value["popupBottom"] + 8 <= value["composerTop"], value
            assert value["popupScrollTop"] > 0 and value["windowScroll"] == 0, value
            if width == 390:
                page.screenshot(
                    path=str(
                        ROOT
                        / "02_구현/전체화면_스파이크/검증결과/실제연구_최종검사/실제연구_알림보완.png"
                    ),
                    full_page=False,
                )
        browser.close()
    after = state()
    assert before["budget"] == after["budget"]
    print(
        json.dumps(
            {
                "status": "PASS",
                "scope": "실제 UI·POST 대역·실호출 없음",
                "measurements": measurements,
                "actual_requests": after["actual_model_requests"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
