"""열린 보고서와 지연 조회가 과거 시점에 남는지 실제 앱에서 검사한다. POST 없음."""

import argparse
import json
import os
import traceback
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.load(response)


def main(base, label):
    output = SPIKE / "검증결과" / label
    output.mkdir(parents=True, exist_ok=True)
    before = get(base, "/api/spike/snapshot")
    run = next(item for item in before["runs"] if item["status"] == "completed")
    report = get(base, f"/api/spike/reports/{run['report_id']}")
    proof = {"checks": [], "posts": [], "base_url": base}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on(
                "request",
                lambda request: (
                    proof["posts"].append(request.url) if request.method == "POST" else None
                ),
            )
            page.goto(base + "/#/control")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.get_by_test_id("command-input").fill("재생 검사에서는 보내지 않을 미전송 지시")

            def open_report():
                close = page.get_by_test_id("popup-close")
                if close.is_visible():
                    close.click()
                if not page.get_by_test_id("drawer").is_visible():
                    page.get_by_test_id("menu-toggle").click()
                page.get_by_test_id("drawer").get_by_role(
                    "button", name="과거 기록", exact=True
                ).click()
                item = (
                    page.get_by_test_id("popup")
                    .locator(".record-list>li")
                    .filter(has_text=run["question"])
                )
                item.get_by_role("button", name="자세히 보기", exact=True).click()
                page.get_by_role("button", name="최종 보고서 보기", exact=True).click()

            def rewind():
                if not page.get_by_test_id("timeline-run-select").is_visible():
                    page.get_by_test_id("timeline-toggle").click()
                page.get_by_test_id("timeline-run-select").select_option(run["run_id"])
                page.get_by_test_id("timeline-scrubber").focus()
                page.get_by_test_id("timeline-scrubber").press("Home")
                page.wait_for_timeout(200)

            open_report()
            page.get_by_test_id("report-viewer").wait_for()
            rewind()
            first = not page.get_by_test_id("report-viewer").is_visible()
            proof["checks"].append({"name": "열린 보고서→제출 이전", "passed": first})
            page.screenshot(path=str(output / "제출이전_팝업.png"))
            assert first, "제출 이전으로 되감았는데 보고서 본문/다운로드가 남았습니다."
            page.get_by_test_id("replay-live").click()
            open_report()
            page.get_by_test_id("report-viewer").wait_for()
            proof["checks"].append({"name": "실시간 명시 재열기", "passed": True})
            page.get_by_test_id("popup-close").click()
            held = []
            page.route(f"**/api/spike/reports/{run['report_id']}", lambda route: held.append(route))
            open_report()
            for _ in range(30):
                if held:
                    break
                page.wait_for_timeout(50)
            assert len(held) == 1, len(held)
            rewind()
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps(report))
            page.wait_for_timeout(300)
            assert not page.get_by_test_id("report-viewer").is_visible(), (
                "되감기 뒤 늦은 보고서 응답이 채택됐습니다."
            )
            proof["checks"].append(
                {"name": "조회 대기→되감기→실제 저장 응답 지연 반환", "passed": True}
            )
            other = next((item for item in before["runs"] if item["run_id"] != run["run_id"]), None)
            if other is not None:
                page.unroute(f"**/api/spike/reports/{run['report_id']}")
                page.get_by_test_id("replay-live").click()
                open_report()
                page.get_by_test_id("report-viewer").wait_for()
                page.get_by_test_id("timeline-run-select").select_option(other["run_id"])
                page.get_by_test_id("timeline-scrubber").press("Home")
                page.wait_for_timeout(200)
                assert not page.get_by_test_id("report-viewer").is_visible(), (
                    "다른 실행 초기 시점에 이전 보고서가 남았습니다."
                )
                proof["checks"].append(
                    {"name": "열린 A 보고서→다른 실행 B 초기 시점", "passed": True}
                )
            assert (
                page.get_by_test_id("command-input").input_value()
                == "재생 검사에서는 보내지 않을 미전송 지시"
            )
            assert not proof["posts"]
            assert get(base, "/api/spike/snapshot") == before
            proof["status"] = "PASS"
            browser.close()
    except Exception as error:  # noqa: BLE001 -- 재현 실패도 그대로 보존한다.
        proof.update(status="FAIL", error=str(error), traceback=traceback.format_exc())
        print(proof["traceback"], flush=True)
    (output / "재생보고서_가시성검사.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("REPLAY_REPORT_VISIBILITY=" + proof["status"], flush=True)
    return 0 if proof["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8768")
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    raise SystemExit(main(args.base_url, args.label))
