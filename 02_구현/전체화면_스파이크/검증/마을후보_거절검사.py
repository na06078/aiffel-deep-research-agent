"""후보 미리보기에서 실제 입력 전송이 안전하게 거절되고 이유가 표시되는지 검사한다."""

import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
BASE = "http://127.0.0.1:8790"
LIVE = "http://127.0.0.1:8768"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def live_fingerprint():
    with urlopen(LIVE + "/api/spike/snapshot", timeout=10) as response:
        state = json.load(response)
    fields = {k: state[k] for k in ("current_run_id", "runs", "queue", "budget")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def main():
    output = SPIKE / "검증결과/마을재설계"
    before = live_fingerprint()
    # 운영에 없는 경로로 선행 차단을 확인한 다음에만 실제 입력창을 누른다.
    try:
        urlopen(Request(BASE + "/api/spike/not-a-real-action", method="POST"), timeout=10)
    except HTTPError as error:
        refused = json.load(error)
        assert error.code == 405 and refused["error"]["code"] == "read_only_preview"
    else:
        raise AssertionError("후보의 변경 요청 차단을 확인하지 못했습니다.")
    proof = {"guard_preflight": "PASS", "checks": [], "unexpected_requests": []}
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})

            def gate(route):
                request = route.request
                allowed = request.url.startswith(BASE + "/") and (
                    request.method == "GET"
                    or (request.method == "POST" and request.url == BASE + "/api/spike/commands")
                )
                if not allowed:
                    proof["unexpected_requests"].append(
                        {"url": request.url, "method": request.method}
                    )
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", gate)
            page.goto(BASE + "/#/control")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            draft = "미리보기 차단 확인: 실제 연구를 만들지 않습니다."
            page.get_by_test_id("command-input").fill(draft)
            with page.expect_response(lambda r: r.url == BASE + "/api/spike/commands") as sent:
                page.get_by_test_id("command-submit").click()
            assert sent.value.status == 405
            notice = page.get_by_role("alert").filter(has_text="read_only_preview")
            notice.wait_for()
            text = notice.inner_text()
            assert "마을 디자인 미리보기에서는 새 연구나 변경 요청을 보내지 않습니다." in text
            assert "undefined" not in text
            assert page.get_by_test_id("command-input").input_value() == draft
            for width, height in ((1440, 900), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_function(
                    """() => {const w=document.querySelector('[data-testid=world]');
                    return Math.abs(Number(w?.dataset.layoutWidth)-innerWidth)<4
                      && Math.abs(Number(w?.dataset.layoutHeight)-innerHeight)<4;}"""
                )
                for selector in ('[data-testid="command-input"]', ".notice-dismiss"):
                    assert page.locator(selector).evaluate(
                        """e => {const r=e.getBoundingClientRect();
                        return r.x>=0&&r.y>=0&&r.right<=innerWidth&&r.bottom<=innerHeight
                          && e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}"""
                    ), {"viewport": [width, height], "unreachable": selector}
                page.screenshot(path=str(output / f"미리보기_차단_{width}_{height}.png"))
            proof["checks"].append(
                {
                    "name": "실제 앱 입력405·코드/한글 사유·초안 보존",
                    "status": "PASS",
                    "message": text,
                }
            )
        finally:
            browser.close()
    assert before == live_fingerprint(), "운영 실행/큐/예산이 바뀌었습니다."
    assert not proof["unexpected_requests"]
    proof.update(status="PASS", live_state_unchanged=True, new_research_calls=0)
    (output / "미리보기_차단_화면확인.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("PREVIEW_REJECTION_UI=PASS LIVE_STATE_UNCHANGED=true NEW_RESEARCH=0", flush=True)


if __name__ == "__main__":
    main()
