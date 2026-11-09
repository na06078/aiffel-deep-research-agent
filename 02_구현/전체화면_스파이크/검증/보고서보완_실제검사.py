"""승인된 실제 원고 보완 버튼을 단1회 실행한다. 기본은 읽기 전용 점검."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

I = Path(__file__).resolve().parents[1]
R = I.parents[1]


def get(base, path):
    with urlopen(base + path, timeout=10) as response:
        return json.load(response)


def main(args):
    assert args.base == "http://127.0.0.1:8798"
    out = R / ".cache/보고서품질_검증/교정실제" / args.label
    assert not out.exists()
    out.mkdir(parents=True)
    proof = {"submitted": False, "requests": [], "captures": [], "errors": []}
    try:
        initial = get(args.base, "/api/spike/snapshot")
        run = next(x for x in initial["runs"] if x["run_id"] == args.run_id)
        assert run["control_revision"] == 1 and run["status"] == "blocked"
        assert not run.get("correction_state") and not run["report_id"]
        assert initial["budget"]["attempt_count"] == 180
        assert initial["budget"]["max_attempts"] == 208 and initial["budget"]["unknown_count"] == 4
        proof["before"] = initial["budget"]
        proof["original_draft_sha256"] = hashlib.sha256(run["draft"].encode()).hexdigest()
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=str(R / ".cache/playwright/chromium-1248/chrome-win64/chrome.exe"), headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            correction_path = f"/api/spike/runs/{args.run_id}/correction"

            def gate(route):
                request = route.request
                if not request.url.startswith(args.base + "/"):
                    proof["errors"].append("nonlocal request")
                    route.abort()
                    return
                if request.method != "GET" and not (args.submit and request.method == "POST" and request.url == args.base + correction_path):
                    proof["errors"].append("unapproved mutation: " + request.url)
                    route.abort()
                    return
                proof["requests"].append({"method": request.method, "url": request.url})
                route.continue_()

            context.route("**/*", gate)
            page = context.new_page()
            page.on("pageerror", lambda error: proof["errors"].append(str(error)))
            page.goto(args.base + "/#/control")
            page.locator('[data-testid="world"][data-render-ready="true"]').wait_for(timeout=20000)
            page.get_by_test_id("menu-toggle").click()
            page.get_by_test_id("drawer").get_by_role("button", name="실행/큐", exact=True).click()
            button = page.get_by_test_id("run-correction")
            button.wait_for()
            button.scroll_into_view_if_needed()
            page.screenshot(path=str(out / "교정_이전.png"))
            proof["captures"].append("교정_이전.png")
            if args.submit:
                with page.expect_response(lambda response: response.url == args.base + correction_path and response.request.method == "POST") as accepted:
                    button.click()
                response = accepted.value
                assert response.status == 202, response.text()
                proof["admission"] = response.json()
                proof["submitted"] = True
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    snap = get(args.base, "/api/spike/snapshot")
                    current = next(x for x in snap["runs"] if x["run_id"] == args.run_id)
                    if current["status"] != "running":
                        break
                    time.sleep(1)
                proof["run_after"] = current
                proof["budget_after"] = snap["budget"]
                assert snap["budget"]["attempt_count"] - initial["budget"]["attempt_count"] <= 2
                assert current["control_revision"] == 2
                assert current["status"] == "completed" and current["report_id"], current.get("quality")
                assert current["quality"]["passed"] and current["quality"]["semantic_review"]["통과"]
                proof["archive"] = get(args.base, f"/api/spike/runs/{args.run_id}/archive")
                (out / "보완_아카이브.json").write_text(json.dumps(proof["archive"], ensure_ascii=False, indent=2), encoding="utf-8")
                page.wait_for_timeout(500)
                page.screenshot(path=str(out / "교정_완료.png"))
                proof["captures"].append("교정_완료.png")
                assert len([x for x in proof["requests"] if x["method"] == "POST"]) == 1
            else:
                after = get(args.base, "/api/spike/snapshot")
                assert after["budget"] == initial["budget"]
            assert not proof["errors"]
            browser.close()
        proof["passed"] = True
    except Exception as error:  # noqa: BLE001 -- 최외곽 증거 경계: 실패를 기록한 뒤 exit 1, 재시도 없음.
        proof["passed"] = False
        proof["error"] = type(error).__name__ + ": " + str(error)
    finally:
        (out / "확인.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": proof.get("passed"), "submitted": proof["submitted"], "proof": str(out / "확인.json"), "error": proof.get("error")}, ensure_ascii=False), flush=True)
    return 0 if proof.get("passed") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8798")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--submit", action="store_true")
    raise SystemExit(main(parser.parse_args()))
