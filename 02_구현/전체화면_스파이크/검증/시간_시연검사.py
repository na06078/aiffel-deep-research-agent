"""사용자 실행본에 명시적인 합성 시연 한 건을 실제 UI로 완료하고 재생한다."""

import hashlib
import importlib.util
import json
import os
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from 화면도우미 import open_replay_from_history

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")
spec = importlib.util.spec_from_file_location("real_checks", SPIKE / "검증/전체화면_실사용검사.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
base = "http://127.0.0.1:8766"


def request_json(path):
    return checks.request_json(base, path)


question = "[합성 시연] 마을 자료실과 정원의 협업·기록 리플레이"
state = request_json("/api/spike/snapshot")
assert (
    not any(r["status"] == "running" for r in state["runs"])
    and not state["queue"]
    and not state["waiting"]
), "사용자 진행 작업을 건드리지 않고 시연 생성 보류"
existing = next(
    (r for r in state["runs"] if r["question"] == question and r["status"] == "completed"), None
)
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base + "/#/control")
    page.wait_for_selector("[data-testid=world][data-render-ready=true]")
    page.screenshot(path=str(SPIKE / "검증결과/시간_시연_마을.png"), full_page=False)

    def control(name):
        close = page.get_by_test_id("popup-close")
        if close.count() and close.is_visible():
            close.click()
        page.get_by_test_id("menu-toggle").click()
        page.get_by_test_id("drawer").get_by_role("button", name="제어 도구", exact=True).click()
        page.get_by_test_id("popup").get_by_role("button", name=name, exact=True).click()
        page.get_by_test_id("popup-close").click()

    if existing is None:
        page.get_by_test_id("command-input").fill(question)
        page.get_by_test_id("command-submit").click()
        checks.wait_state(base, lambda s: len(checks.waits(s)) == 4, "시연 조사4")
        time.sleep(0.8)
        control("현재 1건 완료")
        checks.wait_state(base, lambda s: len(checks.waits(s)) == 3, "시연 첫 응답")
        time.sleep(0.8)
        control("현재 1건 완료")
        checks.wait_state(base, lambda s: len(checks.waits(s)) == 2, "시연 두번째 응답")
        time.sleep(0.8)
        control("현재 모두 완료")
        state = checks.wait_state(
            base, lambda s: checks.current(s)["status"] == "completed", "시연 완료"
        )
        demo = checks.current(state)
    else:
        demo = existing
    page.get_by_test_id("timeline-toggle").click()
    open_replay_from_history(page, demo["run_id"])
    page.get_by_test_id("timeline-scrubber").wait_for()
    slider = page.get_by_test_id("timeline-scrubber")
    slider.press("End")
    page.wait_for_timeout(200)
    page.screenshot(path=str(SPIKE / "검증결과/시간_시연_타임라인.png"), full_page=False)
    before = request_json("/api/spike/snapshot")
    report = request_json(f"/api/spike/reports/{demo['report_id']}")
    slider.press("Home")
    page.wait_for_function(
        "Number(document.querySelector('[data-testid=world]').dataset.sceneTime)===0"
    )
    assert not page.get_by_test_id("paper-document").is_visible()
    page.get_by_test_id("replay-play").click()
    page.wait_for_function(
        "Number(document.querySelector('[data-testid=world]').dataset.sceneTime)>100"
    )
    page.get_by_test_id("replay-play").click()
    page.get_by_test_id("replay-live").click()
    after = request_json("/api/spike/snapshot")
    assert before["mock_requests"] == after["mock_requests"] and after["actual_model_requests"] == 0
    assert report == request_json(f"/api/spike/reports/{demo['report_id']}") and not errors
    with urllib.request.urlopen(base + "/") as response:
        served = response.read()
    assert served == (SPIKE / "화면/dist/index.html").read_bytes()
    browser.close()
proof = {
    "status": "PASS",
    "user_url": base + "/#/control",
    "created_one_labelled_synthetic_demo": existing is None,
    "demo_run_id": demo["run_id"],
    "question": question,
    "report_id": demo["report_id"],
    "report_sha256": report["sha256"],
    "replay_mock_requests_before": before["mock_requests"],
    "replay_mock_requests_after": after["mock_requests"],
    "actual_model_requests": 0,
    "same_served_bundle": True,
    "html_sha256": hashlib.sha256(served).hexdigest(),
    "browser_errors": errors,
    "user_records_preserved": True,
}
(SPIKE / "검증결과/시간_시연_확인.json").write_text(
    json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(proof, ensure_ascii=False))
