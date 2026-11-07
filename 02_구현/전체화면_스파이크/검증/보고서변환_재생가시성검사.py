"""열린 보고서와 지연 조회가 과거 시점에 남는지 실제 앱에서 검사한다. POST 없음."""

import argparse
import json
import os
import traceback
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from 화면도우미 import open_replay_from_history, return_to_live

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.load(response)


def main(base, label, run_id=None):
    output = SPIKE / "검증결과" / label
    output.mkdir(parents=True, exist_ok=True)
    before = get(base, "/api/spike/snapshot")
    run = next(
        item
        for item in before["runs"]
        if item["status"] == "completed" and (run_id is None or item["run_id"] == run_id)
    )
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

            live_run = before.get("current_run_id") == run["run_id"]
            proof["report_run_is_live_current"] = live_run

            def open_timeline():
                if not page.get_by_test_id("timeline-drawer").is_visible():
                    page.get_by_test_id("timeline-toggle").click()
                page.get_by_test_id("timeline-scrubber").wait_for(state="attached")

            def subject_state():
                return page.evaluate(
                    "() => { const e = document.querySelector('[data-testid=\"timeline-subject\"]');"
                    " return e ? {mode: e.dataset.mode, runId: e.dataset.runId || null} : null; }"
                )

            def report_visible():
                return page.get_by_test_id("report-viewer").is_visible()

            def prepare_and_open_report(then=None):
                """보고서를 연다. 되감기 직전 상태까지 만든다(실시간이면 그대로, 아니면 재생 끝에서).

                실시간 현재 실행의 보고서면 실시간에서 열고, 아니면 그 실행의 재생을 과거 기록
                팝업으로 시작해 스크러버 End 키로 끝까지 보낸 뒤 열 수 있다."""
                open_timeline()
                if not live_run:
                    open_replay_from_history(page, run["run_id"])
                    open_timeline()
                    page.get_by_test_id("timeline-scrubber").focus()
                    page.get_by_test_id("timeline-scrubber").press("End")
                    page.wait_for_timeout(200)
                open_report()

            def rewind_with_scrubber():
                """스크러버 Home 키(t=0). 실시간이면 이 키가 읽기 전용 재생을 시작한다."""
                scrubber = page.get_by_test_id("timeline-scrubber")
                scrubber.focus()
                scrubber.press("Home")
                page.wait_for_function(
                    "() => document.querySelector('[data-testid=\"timeline-subject\"]')"
                    "?.dataset.mode === 'replay'",
                    timeout=10000,
                )
                page.wait_for_timeout(200)
                state = subject_state()
                assert state and state["mode"] == "replay", state
                if live_run:
                    assert state["runId"] == run["run_id"], state

            def to_live():
                if subject_state()["mode"] == "replay":
                    return_to_live(page)

            # 지점 1: 열린 보고서 + 스크러버 Home(제출 이전)
            prepare_and_open_report()
            page.get_by_test_id("report-viewer").wait_for()
            assert report_visible(), "되감기 전에 보고서가 열려 있어야 합니다."
            rewind_with_scrubber()
            first = not report_visible()
            proof["checks"].append({"name": "열린 보고서→스크러버 Home(제출 이전)", "passed": first})
            page.screenshot(path=str(output / "제출이전_팝업.png"))
            assert first, "제출 이전으로 되감았는데 보고서 본문/다운로드가 남았습니다."
            return_to_live(page)
            open_report()
            page.get_by_test_id("report-viewer").wait_for()
            proof["checks"].append({"name": "실시간 명시 재열기", "passed": True})
            page.get_by_test_id("popup-close").click()

            # 지점 2: 보고서 응답 보류 → 스크러버 Home → 늦은 응답 반환
            held = []
            page.route(f"**/api/spike/reports/{run['report_id']}", lambda route: held.append(route))
            to_live()
            prepare_and_open_report()
            for _ in range(30):
                if held:
                    break
                page.wait_for_timeout(50)
            assert len(held) == 1, len(held)
            assert not report_visible(), "응답 보류 중에는 보고서가 보이면 안 됩니다."
            rewind_with_scrubber()
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps(report))
            page.wait_for_timeout(300)
            assert not report_visible(), "되감기 뒤 늦은 보고서 응답이 채택됐습니다."
            proof["checks"].append(
                {"name": "조회 대기→스크러버 Home→실제 저장 응답 지연 반환", "passed": True}
            )

            # 지점 3: A 보고서가 열린 채 B 재생 시작 = 팝업 교체(내비게이션)로 닫힘
            other = next((item for item in before["runs"] if item["run_id"] != run["run_id"]), None)
            if other is not None:
                page.unroute(f"**/api/spike/reports/{run['report_id']}")
                to_live()
                open_report()
                page.get_by_test_id("report-viewer").wait_for()
                open_replay_from_history(page, other["run_id"])
                page.wait_for_timeout(200)
                assert not report_visible(), "다른 실행 재생을 시작했는데 이전 보고서가 남았습니다."
                state = subject_state()
                assert state == {"mode": "replay", "runId": other["run_id"]}, state
                proof["checks"].append(
                    {
                        "name": "다른 실행 B 재생 시작 시 A 보고서 팝업이 내비게이션으로 닫힘",
                        "passed": True,
                    }
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
    parser.add_argument("--run-id", default=None, help="검사할 완료 실행 id(기본: 첫 완료 실행)")
    args = parser.parse_args()
    raise SystemExit(main(args.base_url, args.label, args.run_id))
