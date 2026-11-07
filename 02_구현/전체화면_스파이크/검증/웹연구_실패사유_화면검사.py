"""실패한 실행의 사유가 실행/큐·과거 기록·기록 재생 화면에 보이는지 실제 브라우저로 확인한다.

--mode new   : 서버가 만든 진짜 실패(`웹연구_실패사유_서버.py`, 모델 대역이 즉시 실패)를 확인한다.
--mode legacy: 사유 필드가 생기기 전의 실패 기록(읽기 전용, POST 차단)이 안내 문구를 보이는지 확인한다.
외부 요청이 있으면 실패로 판정한다. 결과는 `검증결과/<label>/`에 캡처와 확인.json으로 남긴다.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")

from playwright.sync_api import sync_playwright

EVIDENCE = SPIKE / "검증결과"
QUESTION = "/웹 실패 사유 표시 확인용 질문"
LEGACY_TEXT = "실패 사유가 기록되지 않은 실행입니다"
READY = '[data-testid="world"][data-render-ready="true"]'


def snapshot(base):
    with urllib.request.urlopen(base + "/api/spike/snapshot", timeout=15) as response:
        return json.load(response)


class Check:
    def __init__(self, base, mode, out):
        self.base, self.mode, self.out = base, mode, out
        self.proof = {
            "mode": mode,
            "checks": [],
            "captures": [],
            "browser_errors": [],
            "console_errors": [],
            "external_requests": [],
            "blocked_requests": [],
        }

    def record(self, name, ok, **details):
        self.proof["checks"].append({"name": name, "status": "PASS" if ok else "FAIL", **details})
        print(("PASS " if ok else "FAIL ") + name, details if not ok else "")

    def attach(self, page):
        page.on("pageerror", lambda error: self.proof["browser_errors"].append(str(error)))
        page.on(
            "console",
            lambda m: (
                self.proof["console_errors"].append(m.text[:300]) if m.type == "error" else None
            ),
        )
        page.on(
            "request",
            lambda r: (
                self.proof["external_requests"].append(r.url)
                if r.url.startswith(("http:", "https:")) and not r.url.startswith(self.base + "/")
                else None
            ),
        )
        if self.mode == "legacy":

            def gate(route):
                if route.request.method != "GET":
                    self.proof["blocked_requests"].append(
                        {"method": route.request.method, "url": route.request.url}
                    )
                    route.abort()
                    return
                route.continue_()

            page.route("**/*", gate)

    def panel(self, page, name):
        close = page.get_by_test_id("popup-close")
        if close.count() and close.is_visible():
            close.click()
        if page.get_by_test_id("drawer").is_visible():
            page.keyboard.press("Escape")
        page.get_by_test_id("menu-toggle").click()
        page.get_by_test_id("drawer").get_by_role("button", name=name, exact=True).click()
        page.get_by_test_id("popup").wait_for()

    def notice(self, page):
        element = page.get_by_test_id("popup").get_by_test_id("run-failure")
        element.wait_for(timeout=20000)
        element.scroll_into_view_if_needed()
        return element

    def shot(self, page, name, width, height, element=None):
        page.wait_for_function(
            "() => document.querySelector('[data-testid=popup]')?.getBoundingClientRect().height > 0"
        )
        path = self.out / name
        page.screenshot(path=str(path), full_page=False)
        info = {"file": name, "viewport": [width, height]}
        if element is not None:
            box = element.bounding_box()
            info["notice_box"] = box
            info["inside_viewport"] = bool(
                box
                and box["x"] >= 0
                and box["x"] + box["width"] <= width + 0.5
                and box["y"] >= 0
                and box["y"] + box["height"] <= height + 0.5
            )
        self.proof["captures"].append(info)
        return info

    def expected_text(self, run):
        if self.mode == "legacy":
            return LEGACY_TEXT
        failure = run["failure"]
        return f"실행 실패 사유 · {failure['actor']} · {failure['code']}: {failure['message']}"

    def wait_failed(self):
        deadline = time.time() + 60
        while time.time() < deadline:
            runs = snapshot(self.base)["runs"]
            if runs and runs[0]["status"] == "failed":
                return runs[0]
            time.sleep(0.5)
        raise AssertionError("failed 실행이 60초 안에 생기지 않았다")

    def run(self):
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            self.attach(page)
            page.goto(self.base + "/#/control")
            page.wait_for_selector(READY)
            if self.mode == "new":
                page.get_by_test_id("command-input").fill(QUESTION)
                with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/api/spike/commands")
                ) as information:
                    page.get_by_test_id("command-submit").click()
                self.record("질문 접수", information.value.status == 202 or information.value.ok)
                run = self.wait_failed()
            else:
                runs = snapshot(self.base)["runs"]
                failed = [r for r in runs if r["status"] == "failed"]
                assert failed, "legacy 데이터에 failed 실행이 없다"
                run = failed[-1]
                self.record("legacy 실행에는 failure 필드가 없다", "failure" not in run)
            if self.mode == "new":
                self.record(
                    "서버가 run.failure를 만들었다(코드·문구·역할)",
                    bool(run.get("failure"))
                    and all(run["failure"].get(k) for k in ("code", "message", "actor")),
                    failure=run.get("failure"),
                )
            expected = self.expected_text(run)

            # 1) 실행/큐(실시간)
            self.panel(page, "실행/큐")
            notice = self.notice(page)
            text = notice.inner_text().strip()
            self.record("실행/큐: 실패 사유 문구", expected in text, text=text)
            self.record("실행/큐: role=alert", notice.get_attribute("role") == "alert")
            popup_text = page.get_by_test_id("popup").inner_text()
            self.record("실행/큐: undefined가 보이지 않는다", "undefined" not in popup_text)
            if self.mode == "new":
                self.record(
                    "실행/큐: 서버가 보낸 상한(시도 / 상한)",
                    "논리 검색 시도 0 / 16" in popup_text and "본문 수집 시도 0 / 24" in popup_text,
                    popup=re.findall(r"논리 검색 시도[^\n]*", popup_text),
                )
            else:
                self.record(
                    "실행/큐: 상한 없는 이전 기록은 시도 수만 보인다",
                    "논리 검색 시도 10 ·" in popup_text
                    and "본문 수집 시도 9\n" in popup_text + "\n",
                    popup=re.findall(r"논리 검색 시도[^\n]*", popup_text),
                )
            info = self.shot(page, "실패사유_실행큐_데스크톱.png", 1440, 900, notice)
            self.record("실행/큐: 사유가 화면 안에 보인다", info["inside_viewport"], **info)

            # 2) 과거 기록 상세
            self.panel(page, "과거 기록")
            page.get_by_test_id("popup").get_by_role(
                "button", name="자세히 보기", exact=True
            ).first.click()
            history_notice = self.notice(page)
            self.record(
                "과거 기록 상세: 같은 사유",
                expected in history_notice.inner_text(),
                text=history_notice.inner_text().strip(),
            )
            self.shot(page, "실패사유_과거기록.png", 1440, 900, history_notice)

            # 3) 기록 재생(끝으로 이동): 사유는 사건의 오류에서 다시 만들어진다
            self.panel(page, "과거 기록")
            page.get_by_test_id("popup").get_by_role(
                "button", name="기록 재생", exact=True
            ).first.click()
            scrubber = page.get_by_test_id("timeline-scrubber")
            scrubber.wait_for(timeout=20000)
            scrubber.focus()
            page.keyboard.press("End")
            page.wait_for_timeout(500)
            self.panel(page, "실행/큐")
            replay_notice = self.notice(page)
            replay_text = replay_notice.inner_text().strip()
            events = json.load(
                urllib.request.urlopen(
                    f"{self.base}/api/spike/runs/{run['run_id']}/archive", timeout=15
                )
            )["events"]
            error_events = [e for e in events if e.get("error")]
            ok = bool(error_events) and (
                f"{error_events[-1]['error']['code']}: {error_events[-1]['error']['message']}"
                in replay_text
            )
            self.record("기록 재생(끝): 사건의 오류가 사유로 보인다", ok, text=replay_text)
            self.shot(page, "실패사유_기록재생.png", 1440, 900, replay_notice)

            # 4) 좁은 화면(실시간)
            page.get_by_test_id("popup-close").click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(self.base + "/#/control")
            page.reload()
            page.wait_for_selector(READY)
            self.panel(page, "실행/큐")
            narrow_notice = self.notice(page)
            info = self.shot(page, "실패사유_실행큐_좁은화면.png", 390, 844, narrow_notice)
            self.record(
                "좁은 화면: 사유가 가로로 잘리지 않는다",
                info["inside_viewport"]
                and page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
                **info,
            )
            browser.close()
        self.proof["console_errors"] = [
            m for m in self.proof["console_errors"] if not re.search(r"status of 40[49]", m)
        ]
        problems = [c["name"] for c in self.proof["checks"] if c["status"] != "PASS"]
        if (
            self.proof["browser_errors"]
            or self.proof["console_errors"]
            or self.proof["external_requests"]
        ):
            problems.append("브라우저 오류·콘솔 오류·외부 요청 없음")
        if self.mode == "legacy" and any(
            b["method"] == "POST" for b in self.proof["blocked_requests"]
        ):
            problems.append("legacy 읽기 전용 검사에서 POST 시도")
        self.proof["status"] = "PASS" if not problems else "FAIL"
        self.proof["problems"] = problems
        (self.out / "확인.json").write_text(
            json.dumps(self.proof, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print("SCREEN_CHECK=" + self.proof["status"], problems)
        return 0 if not problems else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--mode", choices=("new", "legacy"), required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    out = EVIDENCE / args.label
    out.mkdir(parents=True, exist_ok=False)  # 기존 증거를 덮어쓰지 않는다.
    return Check(args.base_url.rstrip("/"), args.mode, out).run()


if __name__ == "__main__":
    sys.exit(main())
