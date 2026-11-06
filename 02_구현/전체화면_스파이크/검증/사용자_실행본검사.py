"""사용자에게 남길8766 실행본과 마지막 빌드가 같은지 읽기 전용으로 확인한다."""

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")
base = "http://127.0.0.1:8766"
with urllib.request.urlopen(base + "/") as response:
    html = response.read()
assert html == (SPIKE / "화면/dist/index.html").read_bytes()
assets = re.findall(r'(?:src|href)="(/assets/[^" ]+)"', html.decode())
checks = []
for asset in assets:
    with urllib.request.urlopen(base + asset) as response:
        data = response.read()
    assert data == (SPIKE / "화면/dist" / asset.lstrip("/")).read_bytes()
    checks.append({"asset": asset, "sha256": hashlib.sha256(data).hexdigest()})
with urllib.request.urlopen(base + "/api/health") as response:
    health = json.load(response)
assert health["actual_model_requests"] == 0 and health["provider"] == "test-double"
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/#/control")
    page.wait_for_selector("[data-testid=world][data-render-ready=true]")
    page.get_by_test_id("command-input").wait_for()
    page.get_by_test_id("menu-toggle").click()
    page.get_by_test_id("drawer").get_by_role("button", name="안내", exact=True).click()
    page.get_by_test_id("popup-close").click()
    page.screenshot(path=str(SPIKE / "검증결과/사용자_실행본.png"), full_page=False)
    assert page.evaluate('document.querySelectorAll("canvas").length') == 1 and not errors
    browser.close()
result = {
    "status": "PASS",
    "url": base + "/#/control",
    "health": health,
    "html_sha256": hashlib.sha256(html).hexdigest(),
    "served_assets": checks,
    "browser_errors": errors,
    "operation": "실행 생성 없이 실제 URL·메뉴/팝업·동일 최종 번들을 읽기 전용 확인",
}
(SPIKE / "검증결과/사용자_실행본_확인.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=False))
