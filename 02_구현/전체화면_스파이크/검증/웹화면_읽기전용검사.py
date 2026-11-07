"""후보 정적 화면과 기존 실제 API의 읽기 전용 호환/배치 검사.

브라우저에서 후보 HTML/asset만 가로채며 기존 서버·번들을 바꾸지 않는다.
모든 POST·외부 요청은 전송 전에 차단한다. 새 웹 연구의 종단 간 검사가 아니다.
"""

import argparse
import hashlib
import json
import mimetypes
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
BASE = "http://127.0.0.1:8768"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def snapshot():
    with urlopen(BASE + "/api/spike/snapshot", timeout=10) as response:
        state = json.load(response)
    # 직렬화 전의 사용자 입력/본문을 별도 검사 파일에 복제하지 않는다.
    data = {key: state[key] for key in ("current_run_id", "runs", "queue", "budget")}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return state, digest


def main(assets, label):
    assets = assets.resolve()
    assert assets.is_relative_to(ROOT) and (assets / "index.html").is_file()
    output = SPIKE / "검증결과" / label
    output.mkdir(parents=True, exist_ok=True)
    proof = {
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "후보 화면+기존 실제 API GET; 자료 명령/새 웹 연구/API 통합 미검증",
        "assets": str(assets),
        "checks": [],
        "captures": [],
        "blocked_requests": [],
        "browser_errors": [],
    }
    before, fingerprint = snapshot()
    assert before["provider"] == "openai" and not any(
        run["status"] == "running" for run in before["runs"]
    ), "기존 연구가 실행 중이면 호환 검사를 시작하지 않습니다."
    live_dist = SPIKE / "화면/dist"
    live_hashes = {
        p.relative_to(live_dist).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in live_dist.rglob("*")
        if p.is_file()
    }
    browser = None
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda error: proof["browser_errors"].append(str(error)))

            def route_request(route):
                request = route.request
                if request.method != "GET" or not request.url.startswith(BASE + "/"):
                    proof["blocked_requests"].append({"method": request.method, "url": request.url})
                    route.abort()
                    return
                path = unquote(urlsplit(request.url).path)
                if path == "/" or path.startswith("/assets/"):
                    file = assets / ("index.html" if path == "/" else path.lstrip("/"))
                    assert file.resolve().is_relative_to(assets) and file.is_file(), path
                    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                    route.fulfill(body=file.read_bytes(), content_type=mime)
                    return
                route.continue_()

            page.route("**/*", route_request)
            page.goto(BASE + "/#/control")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.wait_for_function(
                "document.querySelector('[data-testid=drawer]') || "
                "document.querySelector('[data-testid=command-input]')"
            )
            page.evaluate("window.__checkedCanvas=document.querySelector('canvas')")
            assert page.get_by_test_id("source-mode-indicator").count() == 1, (
                "source_mode_indicator_missing"
            )
            page.wait_for_function(
                "document.querySelector('[data-testid=source-mode-indicator]').textContent==='데이터셋'"
            )
            draft = "미전송 입력 보존 검사"
            page.get_by_test_id("command-input").fill(draft)

            def check(name, capture=False):
                page.evaluate("document.fonts.ready")
                page.wait_for_function("""() => {const w=document.querySelector('[data-testid=world]');
                  return Math.abs(Number(w?.dataset.layoutWidth)-innerWidth)<4 &&
                  Math.abs(Number(w?.dataset.layoutHeight)-innerHeight)<4;}""")
                metrics = page.evaluate("""() => {
                  const rect=s=>{const e=document.querySelector(s),r=e?.getBoundingClientRect();
                    return r?{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}:null;};
                  const hit=s=>{const e=document.querySelector(s),r=e?.getBoundingClientRect();
                    return !!r&&e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));};
                  return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
                    scrollX,scrollY,canvasCount:document.querySelectorAll('canvas').length,
                    sameCanvas:window.__checkedCanvas===document.querySelector('canvas'),
                    world:rect('[data-testid=world]'),indicator:rect('[data-testid=source-mode-indicator]'),
                    input:rect('[data-testid=command-input]'),menu:rect('[data-testid=menu-toggle]'),
                    close:rect('[data-testid=popup-close]'),popup:rect('[data-testid=popup]'),
                    menuHit:hit('[data-testid=menu-toggle]'),inputHit:hit('[data-testid=command-input]'),
                    closeHit:!document.querySelector('[data-testid=popup-close]')||hit('[data-testid=popup-close]')};} """)
                if capture:
                    file = output / (name + ".png")
                    page.screenshot(path=str(file), full_page=False)
                    proof["captures"].append(str(file))
                assert metrics["canvasCount"] == 1 and metrics["sameCanvas"], metrics
                assert metrics["scrollWidth"] <= metrics["width"] and not metrics["scrollX"], (
                    metrics
                )
                assert not metrics["scrollY"] and metrics["world"]["x"] == 0, metrics
                assert metrics["world"]["width"] == metrics["width"], metrics
                assert metrics["world"]["height"] == metrics["height"], metrics
                assert metrics["menuHit"] and metrics["inputHit"] and metrics["closeHit"], metrics
                badge = metrics["indicator"]
                for key in ("input", "menu", "close", "popup"):
                    r = metrics[key]
                    overlaps = (
                        r
                        and min(r["right"], badge["right"]) > max(r["x"], badge["x"])
                        and min(r["bottom"], badge["bottom"]) > max(r["y"], badge["y"])
                    )
                    assert not overlaps, {"reason": "indicator_overlap", "target": key, **metrics}
                assert page.get_by_test_id("command-input").input_value() == draft
                if metrics["popup"]:
                    # 보이는 첫 화면과 스크롤 후 마지막 조작 접근을 구별한다.
                    last = page.get_by_test_id("popup").locator(".popup-body button").last
                    if last.count():
                        last.scroll_into_view_if_needed()
                        reachable = last.evaluate("""e => {const r=e.getBoundingClientRect();
                          return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));} """)
                        assert reachable, name
                        metrics["last_popup_button_reachable"] = True
                proof["checks"].append({"name": name, "status": "PASS", **metrics})
                print("PASS " + name, flush=True)

            for width, height in ((1440, 900), (390, 844), (844, 390), (1000, 700)):
                page.set_viewport_size({"width": width, "height": height})
                page.get_by_test_id("timeline-toggle").click()
                handle = page.get_by_test_id("timeline-resize-handle")
                handle.press("End")
                page.wait_for_timeout(120)
                check(f"{width}_{height}_최대타임라인", True)
                page.get_by_test_id("timeline-toggle").click()
                page.get_by_test_id("menu-toggle").click()
                page.get_by_test_id("drawer").get_by_role(
                    "button", name="실행/큐", exact=True
                ).click()
                page.get_by_test_id("popup").wait_for()
                check(f"{width}_{height}_자료팝업", True)
                page.get_by_test_id("timeline-toggle").click()
                handle = page.get_by_test_id("timeline-resize-handle")
                handle.press("End")
                page.wait_for_timeout(120)
                check(f"{width}_{height}_팝업과타임라인")
                page.get_by_test_id("popup-close").click()
                page.get_by_test_id("timeline-toggle").click()
            browser.close()
            browser = None
        _, after = snapshot()
        assert fingerprint == after, "기존 실제 실행/예산/큐 상태가 달라졌습니다."
        assert all(
            hashlib.sha256((live_dist / p).read_bytes()).hexdigest() == h
            for p, h in live_hashes.items()
        )
        assert not proof["blocked_requests"] and not proof["browser_errors"]
        proof.update(
            status="PASS_READ_ONLY_COMPATIBILITY",
            live_state_unchanged=True,
            live_dist_unchanged=True,
        )
    except (AssertionError, OSError, ValueError, PlaywrightError) as error:
        proof.update(status="FAIL", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        (output / "확인.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    main(args.assets, args.label)
