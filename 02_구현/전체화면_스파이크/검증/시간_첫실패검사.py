"""기존 실행본의 정상 렌더 후 타임라인 손잡이 누락을 확인하는 최초 행동 RED."""

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")
result = {"status": "FAIL", "boundary": "정상 렌더된 실제8766 UI에서 새 하단 시간창 손잡이"}
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto("http://127.0.0.1:8766/#/control")
        page.wait_for_selector("[data-testid=world][data-render-ready=true]")
        result["world_rendered"] = True
        result["timeline_toggle_count"] = page.get_by_test_id("timeline-toggle").count()
        browser.close()
        assert result["timeline_toggle_count"] == 1, (
            "정상 마을 화면에 요청한 시간 드로어 손잡이가 없다"
        )
    result["status"] = "PASS"
finally:
    (SPIKE / "검증결과/시간_드로어_최초RED.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
