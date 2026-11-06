"""드로어 하단 행에서의 고정 조작·높이 조절 실제 사용 회귀. GET 전용."""

import argparse
import hashlib
import json
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
BASE = "http://127.0.0.1:8768"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.load(response)


def main(label, baseline):
    output = SPIKE / "검증결과" / label
    output.mkdir(parents=True, exist_ok=True)
    proof = {
        "started_at": datetime.now(UTC).isoformat(),
        "checks": [],
        "captures": [],
        "browser_errors": [],
        "posts": [],
        "failures": [],
        "scope": "기존 실제 완료 기록·읽기 전용 UI 조작; 새 연구/모델 호출 없음",
    }
    before = get("/api/spike/snapshot")
    current = next(r for r in before["runs"] if r["run_id"] == before["current_run_id"])
    assert current["status"] == "completed", "완료된 기록으로만 검사합니다."
    report = get(f"/api/spike/reports/{current['report_id']}")
    db_file = ROOT / "03_실행데이터/실제연구/실행기록.sqlite3"
    db_hash = hashlib.sha256(db_file.read_bytes()).hexdigest()
    proof["budget_before"] = before["budget"]
    proof["report_sha256"] = report["sha256"]
    page = None

    def passed(name, **facts):
        proof["checks"].append({"name": name, "status": "PASS", **facts})
        print("PASS " + name, flush=True)

    def capture(name):
        page.screenshot(path=str(output / name), full_page=False)
        proof["captures"].append(name)

    def settle_layout():
        page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
        page.wait_for_function("""() => {const w=document.querySelector('[data-testid=world]');
          return Math.abs(Number(w?.dataset.layoutWidth)-innerWidth)<4&&Math.abs(Number(w?.dataset.layoutHeight)-innerHeight)<4;}""")
        page.wait_for_timeout(120)

    def world():
        value = page.evaluate("""() => {const w=document.querySelector('[data-testid=world]');
          const c=w.querySelector('canvas'),r=w.getBoundingClientRect();
          const composer=document.querySelector('.composer-area').getBoundingClientRect();
          const drawer=document.querySelector('[data-testid=timeline-drawer]')?.getBoundingClientRect();
          const menu=document.querySelector('[data-testid=menu-toggle]'),mr=menu.getBoundingClientRect();
          return {width:innerWidth,height:innerHeight,canvasCount:document.querySelectorAll('canvas').length,
            sameCanvas:c===window.__drawerCanvas,world:{x:r.x,y:r.y,width:r.width,height:r.height},
            documentWidth:document.documentElement.scrollWidth,scrollX,scrollY,
            composerTop:composer.top,composerBottom:composer.bottom,drawerTop:drawer?.top,
            popupOpen:!!document.querySelector('.popup'),
            menuHit:menu.contains(document.elementFromPoint(mr.x+mr.width/2,mr.y+mr.height/2))};}""")
        assert value["canvasCount"] == 1 and value["sameCanvas"], value
        assert value["world"] == {
            "x": 0,
            "y": 0,
            "width": value["width"],
            "height": value["height"],
        }, value
        assert (
            value["documentWidth"] <= value["width"] and value["scrollX"] == value["scrollY"] == 0
        ), value
        assert value["composerTop"] >= 0, value
        assert value["popupOpen"] or value["menuHit"], ("메뉴 가림", value)
        if value["drawerTop"] is not None:
            assert value["composerBottom"] <= value["drawerTop"], value
        return value

    def hit(locator, name):
        locator.wait_for(state="visible")
        value = locator.evaluate("""e => {const r=e.getBoundingClientRect();
          const x=r.left+r.width/2,y=r.top+r.height/2;const top=document.elementFromPoint(x,y);
          const d=document.querySelector('[data-testid=timeline-drawer]').getBoundingClientRect();
          return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,drawerTop:d.top,drawerBottom:d.bottom,
            inViewport:r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth,
            hit:!!top&&(e===top||e.contains(top))};}""")
        assert value["inViewport"] and value["hit"], (name, value)
        edge_allowance = 8 if name == "resize handle" else 0
        assert (
            value["top"] >= value["drawerTop"] - edge_allowance
            and value["bottom"] <= value["drawerBottom"]
        ), (
            name,
            value,
        )
        return value

    def scroll_element():
        selected = page.get_by_test_id("timeline-track-scroll")
        # 명시적 기존 구현 baseline에서도 같은 행동 반례를 검사한다.
        return selected if selected.count() else page.locator(".timeline-body")

    def scroll_bottom():
        target = scroll_element()
        box = target.bounding_box()
        assert box and box["height"] > 0
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(150)
        target.evaluate("e=>e.scrollTop=e.scrollHeight")
        return target.evaluate("e=>e.scrollTop")

    def assert_pinned():
        for testid in (
            "timeline-run-select",
            "replay-prev",
            "replay-play",
            "replay-next",
            "replay-live",
            "timeline-scrubber",
        ):
            hit(page.get_by_test_id(testid), testid)
        axis = page.locator(".timeline-axis")
        box = axis.bounding_box()
        drawer = page.get_by_test_id("timeline-drawer").bounding_box()
        assert box and drawer and box["y"] >= drawer["y"], ("시간 눈금", box, drawer)
        axis_lane = page.locator(".timeline-axis>div").bounding_box()
        track_lane = page.locator(".track-lanes").first.bounding_box()
        assert abs(axis_lane["x"] - track_lane["x"]) <= 2
        assert abs(axis_lane["width"] - track_lane["width"]) <= 2

    def height():
        return page.get_by_test_id("timeline-drawer").bounding_box()["height"]

    def drag(delta):
        handle = page.get_by_test_id("timeline-resize-handle")
        hit(handle, "resize handle")
        assert handle.evaluate("e=>getComputedStyle(e).cursor") == "ns-resize"
        box = handle.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x, y + delta, steps=10)
        page.mouse.up()
        page.wait_for_timeout(150)
        return height()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, has_touch=True)
        page = context.new_page()
        page.set_default_timeout(6000)
        page.on("pageerror", lambda error: proof["browser_errors"].append(str(error)))
        page.on(
            "request",
            lambda request: (
                proof["posts"].append({"method": request.method, "url": request.url})
                if request.method != "GET"
                else None
            ),
        )
        page.goto(BASE + "/#/control")
        settle_layout()
        page.evaluate("window.__drawerCanvas=document.querySelector('canvas')")
        page.get_by_test_id("command-input").fill("드로어 사용성 검사 중인 미전송 초안")
        page.get_by_test_id("timeline-toggle").click()
        page.get_by_test_id("timeline-run-select").select_option(current["run_id"])
        page.get_by_test_id("timeline-scrubber").wait_for()
        try:
            if not baseline:
                handle = page.get_by_test_id("timeline-resize-handle")
                handle.focus()
                handle.press("Home")
                page.wait_for_timeout(150)
            top = scroll_bottom()
            assert top > 0, "최소 높이에서 실제 트랙 스크롤이 발생해야 합니다."
            capture("타임라인_하단스크롤.png")
            assert_pinned()
            passed("하단 트랙에서도 실행/재생/시점/시간 눈금 즉시 조작", scroll_top=top)
            if baseline:
                raise AssertionError("기존 구현의 반례가 재현되지 않았습니다.")
            scroller = scroll_element()
            for name in ("replay-next", "replay-prev", "replay-play", "replay-play"):
                page.get_by_test_id(name).click()
                page.wait_for_timeout(180)
                assert abs(scroller.evaluate("e=>e.scrollTop") - top) <= 1, name
                assert_pinned()
            slider = page.get_by_test_id("timeline-scrubber")
            slider.focus()
            slider.press("End")
            page.wait_for_timeout(150)
            assert abs(scroller.evaluate("e=>e.scrollTop") - top) <= 1
            assert float(slider.input_value()) == float(slider.get_attribute("max"))
            slider.press("Home")
            assert abs(scroller.evaluate("e=>e.scrollTop") - top) <= 1
            box = slider.bounding_box()
            page.mouse.click(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
            page.wait_for_timeout(150)
            assert 0 < float(slider.input_value()) < float(slider.get_attribute("max"))
            assert abs(scroller.evaluate("e=>e.scrollTop") - top) <= 1
            passed("재생/정지·단계/키보드/포인터 시점 변경 후 같은 행 유지")
            before_height = height()
            bigger = drag(-130)
            assert bigger > before_height + 50, (before_height, bigger)
            world()
            capture("타임라인_높이확대.png")
            page.get_by_test_id("timeline-toggle").click()
            page.get_by_test_id("timeline-toggle").click()
            page.wait_for_timeout(150)
            assert abs(height() - bigger) <= 2
            handle = page.get_by_test_id("timeline-resize-handle")
            assert handle.get_attribute("role") == "separator"
            assert handle.get_attribute("aria-orientation") == "horizontal"
            handle.focus()
            handle.press("ArrowDown")
            page.wait_for_timeout(100)
            assert height() < bigger
            handle.press("ArrowUp")
            page.wait_for_timeout(100)
            assert abs(height() - bigger) <= 2
            passed(
                "경계 커서·마우스 드래그·닫기/재열기·키보드 높이 조절",
                before=before_height,
                after=bigger,
            )
            cdp = context.new_cdp_session(page)
            handle_box = handle.bounding_box()
            x, y = (
                handle_box["x"] + handle_box["width"] / 2,
                handle_box["y"] + handle_box["height"] / 2,
            )
            old = height()
            cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 0}]},
            )
            cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchMove", "touchPoints": [{"x": x, "y": y + 60, "id": 0}]},
            )
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            page.wait_for_timeout(150)
            assert height() < old - 20
            passed("터치 포인터 상단 경계 높이 조절")
            for width, window_height, name in (
                (390, 844, "좁은창"),
                (844, 390, "낮은창"),
                (1440, 900, "넓은창"),
            ):
                page.set_viewport_size({"width": width, "height": window_height})
                settle_layout()
                scroll_bottom()
                assert_pinned()
                world()
                handle = page.get_by_test_id("timeline-resize-handle")
                handle.focus()
                handle.press("End")
                page.wait_for_timeout(150)
                assert_pinned()
                world()
                capture(f"타임라인_{name}.png")
                assert (
                    page.get_by_test_id("command-input").input_value()
                    == "드로어 사용성 검사 중인 미전송 초안"
                )
                passed(name + " 고정 조작·최대 높이·입력·동일 세계")
            # 크게 열린 드로어의 실제 구간 클릭으로 팝업을 연다.
            row = page.get_by_test_id("timeline-track-researcher-4")
            button = row.locator("button.timeline-span").first
            button.scroll_into_view_if_needed()
            button.click()
            page.get_by_test_id("popup").wait_for()
            page.wait_for_timeout(150)
            metrics = world()
            popup_box = page.get_by_test_id("popup").bounding_box()
            assert (
                popup_box["y"] >= 0
                and popup_box["y"] + popup_box["height"] <= metrics["composerTop"] - 8
            )
            capture("타임라인_팝업병행.png")
            page.get_by_test_id("popup-close").click()
            passed("최대 높이에서 원본 상세 팝업·입력 병행")
            assert (
                page.get_by_test_id("command-input").input_value()
                == "드로어 사용성 검사 중인 미전송 초안"
            )
            assert not proof["posts"] and not proof["browser_errors"]
        except Exception as error:  # noqa: BLE001 -- 사용 동선의 반례를 보존한다.
            proof["failures"].append(str(error))
            capture("타임라인_실패상태.png")
            print("FAIL " + str(error), flush=True)
        finally:
            browser.close()
    after = get("/api/spike/snapshot")
    proof.update(
        budget_after=after["budget"],
        db_unchanged=db_hash == hashlib.sha256(db_file.read_bytes()).hexdigest(),
        report_unchanged=report["sha256"]
        == get(f"/api/spike/reports/{current['report_id']}")["sha256"],
    )
    assert (
        before["budget"] == after["budget"] and proof["db_unchanged"] and proof["report_unchanged"]
    )
    proof["status"] = "PASS" if not proof["failures"] else "FAIL"
    proof["finished_at"] = datetime.now(UTC).isoformat()
    (output / "타임라인드로어_실사용검사.json").write_text(
        json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("DRAWER_WORKFLOW=" + proof["status"], flush=True)
    return 0 if proof["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="타임라인드로어_검사")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(args.label, args.baseline))
