"""전체 앱과 구별되는 마을 컴포넌트의 첫 viewport 검사."""

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
SPIKE = Path(__file__).resolve().parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")
out = SPIKE / "검증결과/장면컴포넌트"
out.mkdir(parents=True, exist_ok=True)
result = {
    "scope": "마을 컴포넌트만. 최종 앱/팝업/실제 실행 연결 수용이 아님",
    "errors": [],
    "captures": [],
}
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: result["errors"].append(str(e)))
        page.goto("http://127.0.0.1:8770/장면_미리보기.html")
        page.wait_for_selector("[data-testid=world][data-render-ready=true]", timeout=20000)
        page.wait_for_function('document.querySelectorAll("[data-testid^=npc-]").length === 6')
        for width, height, name in [
            (1440, 900, "장면_넓은화면.png"),
            (390, 844, "장면_좁은화면.png"),
        ]:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_function("""() => {const w=document.querySelector('[data-testid=world]');
              return Math.abs(Number(w?.dataset.layoutWidth)-innerWidth)<4 && Math.abs(Number(w?.dataset.layoutHeight)-innerHeight)<4;}""")
            data = page.evaluate("""() => {const r=document.querySelector('[data-testid=world]').getBoundingClientRect();
              const c=document.querySelector('canvas').getBoundingClientRect(); return {world:{x:r.x,y:r.y,width:r.width,height:r.height},
              canvas:{x:c.x,y:c.y,width:c.width,height:c.height},width:innerWidth,height:innerHeight,
              scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,canvasCount:document.querySelectorAll('canvas').length};}""")
            assert data["world"] == {"x": 0, "y": 0, "width": width, "height": height}, data
            assert data["canvas"] == data["world"] and data["canvasCount"] == 1, data
            assert data["scrollWidth"] == width and data["scrollHeight"] == height, data
            page.screenshot(path=str(out / name), full_page=False)
            result["captures"].append({"file": name, **data})
        page.evaluate('window.__componentCanvas=document.querySelector("canvas")')
        for action, phase in [
            ("move", "at-workstation"),
            ("work", "worked"),
            ("deliver", "submitted"),
        ]:
            page.evaluate(
                '(action)=>window.dispatchEvent(new CustomEvent("scene-check-action",{detail:action}))',
                action,
            )
            page.wait_for_selector(f"[data-testid=world][data-phase={phase}]")
            assert page.evaluate('window.__componentCanvas===document.querySelector("canvas")')
        result["component_actions"] = ["move", "work", "deliver"]
        assert not result["errors"], result["errors"]
        browser.close()
    result["status"] = "PASS"
finally:
    (out / "장면_측정.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
