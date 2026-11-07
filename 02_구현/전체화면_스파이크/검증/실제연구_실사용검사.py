"""승인된 실제 앱의 입력·연구·제출·다운로드·읽기 전용 재생 검사."""

import argparse
import hashlib
import json
import os
import time
import traceback
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from 화면도우미 import open_replay_from_history

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
QUESTION = "1894년에 일어난 주요 사건들은 무엇이고 각각의 원인은 무엇인가?"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=15) as response:
        return json.load(response)


def main(base, submit, label):
    output = SPIKE / "검증결과" / label
    output.mkdir(parents=True, exist_ok=True)
    proof = {
        "started_at": datetime.now(UTC).isoformat(),
        "base_url": base,
        "scope": "실제 gpt-5.6-luna/교안/브라우저/보고서·재생",
        "checks": [],
        "captures": [],
        "browser_errors": [],
        "external_requests": [],
    }
    page = None
    browser = None

    def passed(name, **facts):
        proof["checks"].append({"name": name, "status": "PASS", **facts})
        print("PASS " + name, flush=True)

    def geometry():
        result = page.evaluate("""() => {const w=document.querySelector('[data-testid=world]');
          const c=w?.querySelector('canvas');const r=w?.getBoundingClientRect();
          return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
          scrollHeight:document.documentElement.scrollHeight,scrollX,scrollY,
          world:r?{x:r.x,y:r.y,width:r.width,height:r.height}:null,
          canvasCount:document.querySelectorAll('canvas').length,sameCanvas:window.__checkedCanvas===c};}""")
        assert result["canvasCount"] == 1 and result["sameCanvas"], result
        rect = result["world"]
        assert rect and abs(rect["x"]) < 1 and abs(rect["y"]) < 1, result
        assert abs(rect["width"] - result["width"]) < 1, result
        assert abs(rect["height"] - result["height"]) < 1, result
        assert (
            result["scrollWidth"] <= result["width"] and result["scrollX"] == result["scrollY"] == 0
        ), result
        return result

    def capture(name, width=1440, height=900):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
        page.wait_for_function("""() => {const w=document.querySelector('[data-testid=world]');
          return Math.abs(Number(w?.dataset.layoutWidth)-innerWidth)<4&&Math.abs(Number(w?.dataset.layoutHeight)-innerHeight)<4;}""")
        page.evaluate("document.fonts.ready")
        metrics = geometry()
        page.screenshot(path=str(output / name), full_page=False)
        proof["captures"].append({"file": name, **metrics})

    def close_popup():
        close = page.get_by_test_id("popup-close")
        if close.count() and close.is_visible():
            close.click()
        if page.get_by_test_id("drawer").is_visible():
            page.keyboard.press("Escape")

    def panel(name):
        close_popup()
        page.get_by_test_id("menu-toggle").click()
        page.get_by_test_id("drawer").get_by_role("button", name=name, exact=True).click()
        page.get_by_test_id("popup").wait_for()
        geometry()

    try:
        initial = get(base, "/api/spike/snapshot")
        assert initial["provider"] == "openai" and initial["mode"] == "research"
        assert initial["budget"]["max_attempts"] == 40 and initial["budget"]["max_usd"] == 1
        proof["budget_before"] = initial["budget"]
        if submit:
            assert not initial["runs"], "기존 실제 연구가 있으므로 추가 실행하지 않습니다."
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
            page.on("pageerror", lambda e: proof["browser_errors"].append(str(e)))
            page.on(
                "request",
                lambda r: (
                    proof["external_requests"].append(r.url)
                    if r.url.startswith(("http:", "https:")) and not r.url.startswith(base + "/")
                    else None
                ),
            )
            page.goto(base + "/#/control")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.evaluate("window.__checkedCanvas=document.querySelector('canvas')")
            capture("실제연구_첫화면.png")
            assert not page.locator(".mock-badge").is_visible()
            if submit:
                page.get_by_test_id("command-input").fill(QUESTION)
                page.get_by_test_id("command-submit").click()
                page.wait_for_function(
                    "document.querySelector('[data-testid=command-input]').value==='' "
                )
                passed("실제 화면 입력창에서 교안 기본 질문 전송")
            last_phase = None
            captured = False
            deadline = time.monotonic() + 600
            run = None
            while time.monotonic() < deadline:
                state = get(base, "/api/spike/snapshot")
                run = next(
                    (r for r in state["runs"] if r["run_id"] == state["current_run_id"]), None
                )
                if run:
                    phase = (run["status"], run["phase"], run["actual_model_requests"])
                    if phase != last_phase:
                        print("RESEARCH_STATE " + json.dumps(phase, ensure_ascii=False), flush=True)
                        last_phase = phase
                    if run["phase"] == "researching" and not captured:
                        capture("실제연구_조사진행.png")
                        captured = True
                    if run["status"] != "running":
                        break
                page.wait_for_timeout(1000)
            assert run is not None, "현재 실행이 없습니다."
            # 실제 정본은 실행 데이터에만 둔다. 검사 기록에는 메타데이터와 해시를 남긴다.
            proof["run"] = {key: value for key, value in run.items() if key != "draft"}
            proof["budget_after_research"] = state["budget"]
            if run["status"] != "completed":
                panel("실행/큐")
                capture("실제연구_중단상태.png")
                raise AssertionError(
                    f"연구가 정상 제출되지 않았습니다: {run['status']} / {run['phase']}"
                )
            assert run["quality"]["passed"] and run["actual_model_requests"] > 0
            assert run["mock_requests"] == 0
            run_id = run["run_id"]
            archive = get(base, f"/api/spike/runs/{run_id}/archive")
            proof["run_id"] = run_id
            proof["event_count"] = len(archive["events"])
            proof["research_stop_reason"] = run.get("research_stop_reason")
            operations = [e for e in archive["events"] if e["type"] == "model.completed"]
            assert operations and all(
                e["provider"] == "openai" and e["output"]["request_id"] for e in operations
            )
            passed(
                "실제 모델 응답·6노드·필수 품질 검사와 정본 제출",
                actual_model_responses=len(operations),
            )
            report = get(base, f"/api/spike/reports/{run['report_id']}")
            page.wait_for_timeout(2500)
            page.get_by_test_id("bubble-planner").click()
            page.get_by_test_id("event-detail").wait_for()
            assert "openai" in page.get_by_test_id("event-detail").inner_text()
            capture("실제연구_사건상세.png")
            close_popup()
            page.get_by_test_id("bubble-workstation").click()
            page.get_by_test_id("event-detail").wait_for()
            passed("NPC와 생산 오브젝트 클릭의 실제 사건 상세")
            close_popup()
            page.get_by_test_id("paper-document").click()
            viewer = page.get_by_test_id("report-viewer")
            viewer.wait_for()
            assert viewer.get_by_test_id("report-warnings").is_visible()
            assert "정확성은 다릅니다" in viewer.get_by_test_id("report-warnings").inner_text()
            assert viewer.locator("pre.markdown").inner_text() == report["markdown"]
            with page.expect_download() as information:
                viewer.get_by_role("link", name="Markdown 다운로드", exact=True).click()
            downloaded = information.value
            filename = "🧾 실제 연구 보고서.md"
            downloaded.save_as(str(output / filename))
            content = (output / filename).read_bytes()
            assert content == report["markdown"].encode("utf-8")
            assert hashlib.sha256(content).hexdigest() == report["sha256"]
            proof["report_id"] = report["report_id"]
            proof["report_sha256"] = report["sha256"]
            proof["download_file"] = filename
            passed("제출 문서 클릭·뷰어·다운로드·파일 재열기 동일성", bytes=len(content))
            capture("실제연구_최종보고서.png")
            capture("실제연구_좁은보고서.png", 390, 844)
            close_popup()
            page.set_viewport_size({"width": 1440, "height": 900})
            panel("실행/큐")
            popup_text = page.get_by_test_id("popup").inner_text()
            assert "40" in popup_text and "추정" in popup_text
            capture("실제연구_사용량과검사.png")
            close_popup()
            before = get(base, "/api/spike/snapshot")["budget"]
            data_file = ROOT / "03_실행데이터/실제연구/실행기록.sqlite3"
            db_before = hashlib.sha256(data_file.read_bytes()).hexdigest()
            page.get_by_test_id("timeline-toggle").click()
            assert page.get_by_test_id("replay-status").inner_text() == "실시간"
            open_replay_from_history(page, run_id)
            slider = page.get_by_test_id("timeline-scrubber")
            slider.wait_for()
            for key in ("Home", "End", "Home"):
                slider.focus()
                slider.press(key)
                page.wait_for_timeout(150)
            assert page.get_by_test_id("command-submit").is_disabled()
            assert not page.get_by_test_id("paper-document").is_visible()
            capture("실제연구_읽기전용재생.png")
            page.get_by_test_id("replay-next").click()
            page.get_by_test_id("replay-play").click()
            page.wait_for_timeout(350)
            page.get_by_test_id("replay-play").click()
            page.reload()
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.evaluate("window.__checkedCanvas=document.querySelector('canvas')")
            assert page.get_by_test_id("command-submit").is_disabled()
            page.get_by_test_id("replay-live").click()
            panel("과거 기록")
            page.get_by_test_id("popup").get_by_role(
                "button", name="자세히 보기", exact=True
            ).first.click()
            assert QUESTION in page.get_by_test_id("popup").inner_text()
            after = get(base, "/api/spike/snapshot")["budget"]
            assert before == after
            assert db_before == hashlib.sha256(data_file.read_bytes()).hexdigest()
            assert (
                report["sha256"] == get(base, f"/api/spike/reports/{report['report_id']}")["sha256"]
            )
            proof["readonly_budget_before"] = before
            proof["readonly_budget_after"] = after
            proof["readonly_db_sha256"] = db_before
            passed("재생·시점 이동·새로고침·실시간 복귀·과거 기록의 호출/DB/정본 불변")
            assert not proof["browser_errors"] and not proof["external_requests"], proof
            proof["status"] = "PASS"
            browser.close()
            browser = None
    except Exception as error:  # noqa: BLE001 -- 검증 실패를 증거로 보존한다.
        proof["status"] = "FAIL"
        proof["error"] = str(error)
        proof["traceback"] = traceback.format_exc()
        print(proof["traceback"], flush=True)
    finally:
        proof["finished_at"] = datetime.now(UTC).isoformat()
        (output / "실제연구_실사용검사.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("ACTUAL_RESEARCH_BROWSER=" + proof["status"], flush=True)
    return 0 if proof["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8768")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--label", default="실제연구_첫검사")
    args = parser.parse_args()
    raise SystemExit(main(args.base_url, args.submit, args.label))
