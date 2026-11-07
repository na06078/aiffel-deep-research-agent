"""기존 실제 연구 기록으로 마을 후보의 렌더·클릭·재생을 읽기 전용 검사한다.

새 연구/모델/검색/변환 생성은 하지 않는다. 공통 브라우저/DB 검사기는 재사용하며
후보/기존 실행본을 같은 viewport·같은 저장 시점에서 비교할 수 있게 캡처한다.
"""

import argparse
import hashlib
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "web_readonly_checks", SPIKE / "검증/웹연구_실사용검사.py"
)
COMMON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMON)


def manifest(directory):
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def seek(page, value):
    slider = page.get_by_test_id("timeline-scrubber")
    slider.evaluate(
        """(e,v) => {Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,String(v));
        e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}""",
        value,
    )
    page.wait_for_function(
        "v => Math.abs(Number(document.querySelector('[data-testid=world]').dataset.sceneTime)-v)<2",
        arg=value,
    )


def positions(page):
    return page.locator('[data-testid^="npc-"]').evaluate_all(
        "es => es.map(e => ({id:e.dataset.testid,x:e.dataset.x,y:e.dataset.y,status:e.dataset.status}))"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8790")
    parser.add_argument("--label", default="마을재설계/실사용_1차")
    parser.add_argument("--capture-only", action="store_true")
    args = parser.parse_args()
    assert args.base.startswith("http://127.0.0.1:")
    output = (SPIKE / "검증결과" / args.label).resolve()
    assert output.is_relative_to(SPIKE / "검증결과/마을재설계")
    data_dir = ROOT / "03_실행데이터/실제연구"
    check = COMMON.Check(args.base, args.label, "readonly", data_dir, data_dir / "호출예산.sqlite3")
    check.proof["scope"] = "실제 기존 연구 GET·기록 재생; 새 연구/모델/검색/변환 생성 없음"
    before = check.fingerprint()
    live_dist = manifest(SPIKE / "화면/dist")
    live_data = manifest(data_dir)
    snapshot = check.api("/api/spike/snapshot")
    assert snapshot["provider"] == "openai" and not any(
        r["status"] == "running" for r in snapshot["runs"]
    )
    run = next(
        r
        for r in snapshot["runs"]
        if r["status"] == "completed" and r.get("source_mode", "local") == "local"
    )
    archive = check.api(f"/api/spike/runs/{run['run_id']}/archive")
    origin = datetime.fromisoformat(archive["started_at"])
    offsets = {
        e["event_id"]: round(
            (datetime.fromisoformat(e["occurred_at"]) - origin).total_seconds() * 1000
        )
        for e in archive["events"]
    }
    assignment = next(e for e in archive["events"] if e["type"] == "assignment.created")
    submitted = next(e for e in archive["events"] if e["type"] == "report.submitted")
    middle = offsets[assignment["event_id"]] + 2000
    transport = offsets[submitted["event_id"]] + 1100
    end = max(round(archive["duration_ms"]), offsets[submitted["event_id"]] + 2200)
    browser = None
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            check.attach(browser)
            page = check.page
            page.set_default_timeout(12000)
            check.open_page()
            draft = "미전송 디자인 검수 입력"
            page.get_by_test_id("command-input").fill(draft)
            for width, height in ((1440, 900), (1920, 1080), (390, 844), (844, 390), (1000, 700)):

                def viewport(w=width, h=height):
                    check.capture(f"실제상태_{w}_{h}.png", w, h)
                    assert page.get_by_test_id("command-input").input_value() == draft
                    return {"width": w, "height": h}

                check.step(f"실제상태 전체 화면·입력·메뉴 {width}×{height}", viewport)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_function(COMMON.LAYOUT_READY_JS)
            if not args.capture_only:

                def details():
                    ids = []
                    for testid in (
                        "bubble-planner",
                        "bubble-researcher-1",
                        "bubble-researcher-2",
                        "bubble-researcher-3",
                        "bubble-researcher-4",
                        "bubble-editor",
                        "bubble-workstation",
                    ):
                        target = page.get_by_test_id(testid)
                        if not target.count() or not target.is_visible():
                            continue
                        target.click()
                        detail = page.get_by_test_id("event-detail")
                        detail.wait_for()
                        event_id = detail.locator("dd").first.inner_text().strip()
                        actual = check.api(f"/api/spike/events/{event_id}")
                        assert actual["run_id"] in {r["run_id"] for r in snapshot["runs"]}
                        assert actual["event_id"] == event_id
                        assert (
                            "프롬프트 · 입력" in detail.inner_text()
                            and "출력" in detail.inner_text()
                        )
                        ids.append(
                            {"control": testid, "event_id": event_id, "actor": actual["actor"]}
                        )
                        check.close_popup()
                    assert len(ids) >= 5, ids
                    return {"opened_events": ids}

                check.step("NPC/생산 말풍선은 실제 보존 사건 상세를 연다", details)

                def history_and_report():
                    viewer = check.open_history_report(run["run_id"])
                    report = check.api(f"/api/spike/reports/{run['report_id']}")
                    assert viewer.locator("pre.markdown").inner_text() == report["markdown"]
                    folder = ROOT / ".cache/마을재설계_다운로드"
                    folder.mkdir(parents=True, exist_ok=True)
                    with page.expect_download() as downloaded:
                        viewer.get_by_role("link", name="Markdown 다운로드", exact=True).click()
                    target = folder / "🧾 검사용 보고서.md"
                    downloaded.value.save_as(str(target))
                    assert hashlib.sha256(target.read_bytes()).hexdigest() == report["sha256"]
                    check.capture("실제기록_보고서_열기.png")
                    check.close_popup()
                    return {"report_id": report["report_id"], "sha256": report["sha256"]}

                check.step("과거 기록 → 실행 상세 → 보고서 → 다운로드 재열기", history_and_report)
            page.get_by_test_id("timeline-toggle").click()
            page.get_by_test_id("timeline-run-select").select_option(run["run_id"])
            page.get_by_test_id("timeline-scrubber").wait_for()
            seek(page, 0)
            assert page.get_by_test_id("command-submit").is_disabled()
            page.get_by_test_id("timeline-toggle").click()
            for width, height in ((1440, 900), (390, 844), (844, 390)):
                check.capture(f"지도_기록시작_{width}_{height}.png", width, height)
            if not args.capture_only:
                page.set_viewport_size({"width": 1440, "height": 900})
                page.wait_for_function(COMMON.LAYOUT_READY_JS)
                page.get_by_test_id("timeline-toggle").click()

                def replay():
                    seen = []
                    samples = {}
                    for value in (
                        0,
                        offsets[assignment["event_id"]] + 650,
                        middle,
                        transport,
                        end,
                        middle,
                    ):
                        seek(page, value)
                        points = positions(page)
                        if value in samples:
                            assert samples[value] == points, {
                                "at": value,
                                "before": samples[value],
                                "after": points,
                            }
                        samples[value] = points
                        seen.append(
                            {
                                "at_ms": value,
                                "points": points,
                                "phase": page.get_by_test_id("world").get_attribute("data-phase"),
                            }
                        )
                        assert page.get_by_test_id("command-submit").is_disabled()
                    assert samples[0] != samples[middle], "업무 시작 전/후 NPC 위치가 같음"
                    assert not page.get_by_test_id("paper-document").count(), (
                        "제출 이전 시점에 문서가 보임"
                    )
                    seek(page, end)
                    page.get_by_test_id("timeline-toggle").click()
                    page.get_by_test_id("paper-document").click()
                    page.get_by_test_id("report-viewer").wait_for()
                    assert run["report_id"] in page.get_by_test_id("report-viewer").inner_text()
                    check.close_popup()
                    page.get_by_test_id("timeline-toggle").click()
                    seek(page, transport)
                    check.capture("실제기록_문서운반.png")
                    seek(page, middle)
                    check.capture("실제기록_병렬작업.png")
                    page.get_by_test_id("replay-live").click()
                    page.get_by_test_id("timeline-toggle").click()
                    assert page.get_by_test_id("command-input").input_value() == draft
                    return {"run_id": run["run_id"], "samples": seen}

                check.step("실제기록의 이동·운반·재방문 같은 위치·미래 문서 차단", replay)

                def overlays():
                    for width, height in ((1440, 900), (390, 844), (844, 390)):
                        page.set_viewport_size({"width": width, "height": height})
                        page.wait_for_function(COMMON.LAYOUT_READY_JS)
                        check.panel("실행/큐")
                        check.capture(f"팝업_접근_{width}_{height}.png", width, height)
                        check.close_popup()
                    page.reload()
                    check.wait_ready()
                    assert (
                        check.api("/api/spike/snapshot")["current_run_id"]
                        == snapshot["current_run_id"]
                    )
                    return {"viewports": 3, "same_current_run": True}

                check.step("메뉴/팝업/닫기·새로고침은 기존 실행을 보존", overlays)
            browser.close()
            browser = None
        after = check.fingerprint()
        assert after == before, {"before": before, "after": after}
        assert manifest(SPIKE / "화면/dist") == live_dist, "기존 실행본 번들 변경"
        assert manifest(data_dir) == live_data, "기존 실행 데이터 바이트 변경"
        check.proof.update(
            live_dist_unchanged=True, live_data_unchanged=True, api_db_unchanged=True
        )
        check.step(
            "기존 실행/큐/예산/DB/보고서/8768 번들 불변·POST/외부0",
            lambda: {
                "budget": snapshot["budget"],
                "protected_dist_files": len(live_dist),
                "protected_data_files": len(live_data),
            },
        )
    except Exception as error:
        check.proof["fatal"] = f"{type(error).__name__}: {error}"[:1400]
        if not any(c["status"] == "FAIL" for c in check.proof["checks"]):
            check.proof["checks"].append(
                {"name": "검사 전체", "status": "FAIL", "error": check.proof["fatal"]}
            )
        if check.page and browser:
            try:
                check.page.screenshot(path=str(check.output / "실패_현재화면.png"), full_page=False)
            except (OSError, PlaywrightError):
                pass
        raise
    finally:
        if browser:
            browser.close()
        status = check.finish()
    return status


if __name__ == "__main__":
    sys.exit(main())
