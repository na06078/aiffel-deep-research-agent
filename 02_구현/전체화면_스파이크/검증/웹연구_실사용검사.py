"""웹 조사 모드의 실제 브라우저 검사.

phase
- readonly: 기존(레거시) 데이터 위의 새 코드와 화면. GET만 허용하고 모든 POST는 전송 전에 차단한다.
            모델·검색·본문 요청 0.
- zero:     비어 있는 격리 서버의 자료 모드 단독 명령(/웹, /디비). 모델·검색·본문 요청 0.
- research: 비어 있는 격리 서버의 실제 웹 연구 1건. --submit이 있어야 전송한다.
            --verify-only는 이미 끝난 실행을 다시 검증만 하며 아무것도 전송하지 않는다.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
import traceback
import urllib.request
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from 화면도우미 import open_replay_from_history

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")
QUESTION = "Python asyncio의 TaskGroup과 gather는 오류 처리와 작업 취소가 어떻게 다른가?"
# 기획1 + 첫 4절(선택3+읽기3+원고1) + 추가 4절(검색어1+선택3+읽기3+원고1) + 종합1
MODEL_REQUEST_CEILING = 62
RUN_TIMEOUT_SECONDS = 900
EXPECTED_REJECTION = re.compile(r"status of (409|422)\b")
GEOMETRY_JS = """() => {
  const rect = s => {const e = document.querySelector(s), r = e?.getBoundingClientRect();
    return r ? {x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom} : null;};
  const hit = s => {const e = document.querySelector(s), r = e?.getBoundingClientRect();
    return !!r && e.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));};
  const w = document.querySelector('[data-testid=world]');
  return {width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth,
    scrollX, scrollY, canvasCount: document.querySelectorAll('canvas').length,
    sameCanvas: window.__checkedCanvas === w?.querySelector('canvas'),
    world: rect('[data-testid=world]'), indicator: rect('[data-testid=source-mode-indicator]'),
    input: rect('[data-testid=command-input]'), menu: rect('[data-testid=menu-toggle]'),
    popup: rect('[data-testid=popup]'), close: rect('[data-testid=popup-close]'),
    menuHit: hit('[data-testid=menu-toggle]'), inputHit: hit('[data-testid=command-input]'),
    indicatorHit: hit('[data-testid=source-mode-indicator]')};
}"""
LAYOUT_READY_JS = """() => {const w = document.querySelector('[data-testid=world]');
  return Math.abs(Number(w?.dataset.layoutWidth) - innerWidth) < 4
    && Math.abs(Number(w?.dataset.layoutHeight) - innerHeight) < 4;}"""


def now():
    return datetime.now(UTC).isoformat()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.load(response)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def ro_connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    return db


def db_fingerprint(*paths):
    """읽기 전용으로 모든 테이블의 행 수와 내용 해시를 구한다(WAL에 커밋된 내용 포함)."""
    result = {}
    for path in paths:
        path = Path(path)
        if not path.is_file():
            result[path.name] = None
            continue
        with closing(ro_connect(path)) as db:
            tables = [
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
                )
            ]
            entry = {}
            for table in tables:
                digest, count = hashlib.sha256(), 0
                for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                    digest.update(repr(row).encode())
                    count += 1
                entry[table] = [count, digest.hexdigest()[:16]]
            result[path.name] = entry
    return result


class StepFailed(Exception):
    pass


class Check:
    def __init__(self, base, label, phase, data_dir, ledger):
        self.base = base
        self.output = SPIKE / "검증결과" / label
        self.output.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir) if data_dir else None
        self.ledger = Path(ledger) if ledger else None
        self.readonly = phase == "readonly"
        self.page = None
        self.proof = {
            "started_at": now(),
            "phase": phase,
            "base_url": base,
            "checks": [],
            "captures": [],
            "browser_errors": [],
            "console_errors": [],
            "external_requests": [],
            "blocked_requests": [],
        }

    # ---- 공통 도구 -------------------------------------------------------------------
    def step(self, name, function, must=True):
        try:
            facts = function() or {}
        except Exception as error:
            # 실패를 증거로 보존하고 계속할지는 호출자(must)가 정한다.
            self.proof["checks"].append(
                {
                    "name": name,
                    "status": "FAIL",
                    "error": f"{type(error).__name__}: {error}"[:700],
                    "traceback": traceback.format_exc()[-1600:],
                }
            )
            print("FAIL " + name, flush=True)
            if must:
                raise StepFailed(name) from error
            return False
        self.proof["checks"].append({"name": name, "status": "PASS", **facts})
        print("PASS " + name, flush=True)
        return True

    def api(self, path):
        return get(self.base, path)

    def db_paths(self):
        paths = []
        if self.data_dir:
            paths += [self.data_dir / "실행기록.sqlite3", self.data_dir / "웹자료/자료.sqlite3"]
        if self.ledger:
            paths.append(self.ledger)
        return paths

    def fingerprint(self):
        snapshot = self.api("/api/spike/snapshot")
        light = {
            key: snapshot.get(key)
            for key in ("selected_source_mode", "current_run_id", "queue", "budget")
        }
        light["runs"] = [
            {
                key: run.get(key)
                for key in (
                    "run_id",
                    "status",
                    "phase",
                    "control_revision",
                    "source_mode",
                    "actual_model_requests",
                    "web_usage",
                )
            }
            for run in snapshot["runs"]
        ]
        text = json.dumps(light, sort_keys=True, ensure_ascii=False)
        return {"api": sha256_bytes(text.encode())[:16], "db": db_fingerprint(*self.db_paths())}

    def attach(self, browser):
        page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
        page.on("pageerror", lambda error: self.proof["browser_errors"].append(str(error)))
        page.on(
            "console",
            lambda message: (
                self.proof["console_errors"].append(message.text[:300])
                if message.type == "error"
                else None
            ),
        )
        page.on(
            "request",
            lambda request: (
                self.proof["external_requests"].append(request.url)
                if request.url.startswith(("http:", "https:"))
                and not request.url.startswith(self.base + "/")
                else None
            ),
        )
        if self.readonly:

            def gate(route):
                request = route.request
                if request.method != "GET":
                    self.proof["blocked_requests"].append(
                        {"method": request.method, "url": request.url}
                    )
                    route.abort()
                    return
                route.continue_()

            page.route("**/*", gate)
        self.page = page

    def wait_ready(self):
        self.page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
        self.page.evaluate("window.__checkedCanvas=document.querySelector('canvas')")

    def open_page(self):
        self.page.goto(self.base + "/#/control")
        self.wait_ready()

    def indicator(self):
        return self.page.get_by_test_id("source-mode-indicator").inner_text().strip()

    def expect_indicator(self, text, timeout=20000):
        self.page.wait_for_function(
            "(t) => document.querySelector('[data-testid=source-mode-indicator]')"
            "?.textContent.trim() === t",
            arg=text,
            timeout=timeout,
        )

    def geometry(self):
        metrics = self.page.evaluate(GEOMETRY_JS)
        assert metrics["canvasCount"] == 1 and metrics["sameCanvas"], metrics
        world = metrics["world"]
        assert world and abs(world["x"]) < 1 and abs(world["y"]) < 1, metrics
        assert abs(world["width"] - metrics["width"]) < 1, metrics
        assert abs(world["height"] - metrics["height"]) < 1, metrics
        assert metrics["scrollWidth"] <= metrics["width"], metrics
        assert metrics["scrollX"] == metrics["scrollY"] == 0, metrics
        assert metrics["menuHit"] and metrics["inputHit"], metrics
        badge = metrics["indicator"]
        assert badge, metrics
        for key in ("input", "menu", "close", "popup"):
            rect = metrics[key]
            overlaps = (
                rect
                and min(rect["right"], badge["right"]) > max(rect["x"], badge["x"])
                and min(rect["bottom"], badge["bottom"]) > max(rect["y"], badge["y"])
            )
            assert not overlaps, {"reason": "indicator_overlap", "target": key, **metrics}
        return metrics

    def capture(self, name, width=1440, height=900):
        page = self.page
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
        page.wait_for_function(LAYOUT_READY_JS)
        page.evaluate("document.fonts.ready")
        metrics = self.geometry()
        page.screenshot(path=str(self.output / name), full_page=False)
        self.proof["captures"].append(
            {
                "file": name,
                "viewport": [width, height],
                "indicator_text": self.indicator(),
                "indicator": metrics["indicator"],
            }
        )
        return metrics

    def close_popup(self):
        close = self.page.get_by_test_id("popup-close")
        if close.count() and close.is_visible():
            close.click()
        if self.page.get_by_test_id("drawer").is_visible():
            self.page.keyboard.press("Escape")

    def panel(self, name):
        self.close_popup()
        self.page.get_by_test_id("menu-toggle").click()
        self.page.get_by_test_id("drawer").get_by_role("button", name=name, exact=True).click()
        self.page.get_by_test_id("popup").wait_for()

    def send(self, text):
        page = self.page
        page.get_by_test_id("command-input").fill(text)
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/api/spike/commands")
        ) as information:
            page.get_by_test_id("command-submit").click()
        response = information.value
        return response.status, response.json()

    def open_history_report(self, run_id=None):
        self.panel("과거 기록")
        items = self.page.get_by_test_id("popup").locator('[data-testid="history-item"]')
        target = (
            self.page.get_by_test_id("popup").locator(
                f'[data-testid="history-item"][data-run-id={json.dumps(run_id)}]'
            )
            if run_id
            else items.last
        )
        target.get_by_role("button", name="자세히 보기", exact=True).click()
        self.page.get_by_role("button", name="최종 보고서 보기", exact=True).click()
        viewer = self.page.get_by_test_id("report-viewer")
        viewer.wait_for()
        return viewer

    def download(self, locator, name):
        with self.page.expect_download() as information:
            locator.click()
        target = self.output / name
        information.value.save_as(str(target))
        return target

    def finish(self):
        problems = [c["name"] for c in self.proof["checks"] if c["status"] != "PASS"]
        # 일부러 보낸 거절 요청(409·422)은 브라우저가 콘솔 오류로 남긴다. 그 밖의 오류만 문제로 본다.
        expected = [m for m in self.proof["console_errors"] if EXPECTED_REJECTION.search(m)]
        console = [m for m in self.proof["console_errors"] if not EXPECTED_REJECTION.search(m)]
        self.proof["expected_rejection_console_messages"] = len(expected)
        self.proof["console_errors"] = console
        if self.proof["browser_errors"] or self.proof["external_requests"] or console:
            problems.append("browser_or_network_errors")
        if self.proof["blocked_requests"]:
            problems.append("blocked_requests")
        self.proof["status"] = "PASS" if not problems else "FAIL"
        self.proof["problems"] = problems
        self.proof["finished_at"] = now()
        (self.output / "확인.json").write_text(
            json.dumps(self.proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("WEB_BROWSER_CHECK=" + self.proof["status"], flush=True)
        return 0 if self.proof["status"] == "PASS" else 1

    # ---- readonly -------------------------------------------------------------------
    def phase_readonly(self):
        initial = self.api("/api/spike/snapshot")

        def snapshot_facts():
            assert initial["provider"] == "openai" and initial["mode"] == "research"
            assert initial["source_modes"] == ["local", "web"], initial.get("source_modes")
            assert initial["selected_source_mode"] == "local"
            assert initial["runs"], "기존 실행이 없어 레거시 호환을 검사할 수 없습니다."
            assert not any(run["status"] == "running" for run in initial["runs"])
            return {
                "selected_source_mode": initial["selected_source_mode"],
                "source_modes": initial["source_modes"],
                "runs": [
                    {k: run.get(k) for k in ("run_id", "status", "source_mode", "control_revision")}
                    for run in initial["runs"]
                ],
                "legacy_runs_without_source_mode": sum(
                    "source_mode" not in run for run in initial["runs"]
                ),
                "budget": initial["budget"],
            }

        self.step("새 코드의 스냅샷: 자료 모드 목록·기본 선택·기존 실행 보존", snapshot_facts)
        before = self.fingerprint()
        run_id = initial["runs"][-1]["run_id"]
        question = initial["runs"][-1]["question"]
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            self.attach(browser)
            try:

                def first_screen():
                    self.open_page()
                    self.expect_indicator("데이터셋")
                    wide = self.capture("읽기전용_첫화면.png")
                    self.capture("읽기전용_좁은화면.png", 390, 844)
                    return {"indicator_rect": wide["indicator"]}

                self.step("첫 화면: 표시기 '데이터셋'·전체 화면 마을·겹침 없음", first_screen)

                def history():
                    self.page.set_viewport_size({"width": 1440, "height": 900})
                    self.panel("과거 기록")
                    # 새 목록은 실행 id 대신 자료 모드 칩(data-kind=source)과 data-run-id를 쓴다.
                    item = self.page.get_by_test_id("popup").locator(
                        f'[data-testid="history-item"][data-run-id={json.dumps(run_id)}]'
                    )
                    text = item.inner_text()
                    chip = item.locator('[data-kind="source"]').inner_text().strip()
                    assert question in text and chip == "데이터셋", (chip, text[:400])
                    self.capture("읽기전용_과거기록.png")
                    item.get_by_role("button", name="자세히 보기", exact=True).click()
                    detail = self.page.get_by_test_id("popup").inner_text()
                    assert "자료" in detail and "데이터셋" in detail, detail[:400]
                    return {"list_has_dataset_label": True}

                self.step("과거 기록: 기존 실행이 데이터셋으로 표시", history)

                def report():
                    viewer = self.open_history_report(run_id)
                    api_report = self.api("/api/spike/reports/" + initial["runs"][-1]["report_id"])
                    assert viewer.locator("pre.markdown").inner_text() == api_report["markdown"]
                    target = self.download(
                        viewer.get_by_role("link", name="Markdown 다운로드", exact=True),
                        "읽기전용_기존보고서.md",
                    )
                    data = target.read_bytes()
                    assert data == api_report["markdown"].encode("utf-8")
                    assert sha256_bytes(data) == api_report["sha256"]
                    return {"report_sha256": api_report["sha256"], "bytes": len(data)}

                self.step("기존 보고서: 뷰어·다운로드 파일이 정본과 같음", report)

                def usage():
                    self.panel("실행/큐")
                    text = self.page.get_by_test_id("popup").inner_text()
                    budget = initial["budget"]
                    expect = f"시도 {budget['attempt_count']} / {budget['max_attempts']}"
                    assert expect in text and "추정" in text, text[:500]
                    self.capture("읽기전용_실행큐.png")
                    return {"budget_line": expect}

                self.step("실행/큐: 호출 예산 표시", usage)

                def replay():
                    self.close_popup()
                    page = self.page
                    page.get_by_test_id("timeline-toggle").click()
                    assert page.get_by_test_id("replay-status").inner_text() == "실시간"
                    open_replay_from_history(page, run_id)
                    slider = page.get_by_test_id("timeline-scrubber")
                    slider.wait_for()
                    seen = {}
                    for key in ("Home", "End"):
                        slider.focus()
                        slider.press(key)
                        page.wait_for_timeout(250)
                        seen[key] = self.indicator()
                    assert seen["End"] == "데이터셋", seen
                    assert page.get_by_test_id("command-submit").is_disabled()
                    self.capture("읽기전용_재생.png")
                    page.get_by_test_id("replay-live").click()
                    return {"indicator_at_home_and_end": seen}

                self.step("읽기 전용 재생: 레거시 실행의 시점별 표시기", replay)

                def reload_state():
                    self.page.reload()
                    self.wait_ready()
                    self.expect_indicator("데이터셋")
                    return {}

                self.step("새로고침 후 표시기 유지", reload_state)
            finally:
                browser.close()

        def unchanged():
            after = self.fingerprint()
            assert after == before, {"before": before, "after": after}
            assert not self.proof["blocked_requests"], self.proof["blocked_requests"]
            return {"fingerprint": after["api"], "blocked_post_requests": 0}

        self.step("실행·큐·예산·DB·장부가 검사 전과 같음(POST 0)", unchanged, must=False)

    # ---- zero -----------------------------------------------------------------------
    def phase_zero(self):
        initial = self.api("/api/spike/snapshot")

        def snapshot_facts():
            assert initial["source_modes"] == ["local", "web"], initial.get("source_modes")
            assert initial["selected_source_mode"] == "local" and not initial["runs"]
            return {"budget": initial["budget"]}

        self.step("빈 격리 서버: 기본 선택 데이터셋·실행 0", snapshot_facts)
        base_attempts = initial["budget"]["attempt_count"]
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            self.attach(browser)
            try:
                self.open_page()
                self.expect_indicator("데이터셋")
                self.capture("무비용_01_초기.png")

                def mode_only(text, expected_mode, label, shot):
                    def run():
                        status, body = self.send(text)
                        assert status == 202, (status, body)
                        assert body["kind"] == "mode_change" and body["status"] == "applied", body
                        assert body["source_mode"] == expected_mode and body.get("run_id") is None
                        self.expect_indicator(label)
                        state = self.api("/api/spike/snapshot")
                        assert state["selected_source_mode"] == expected_mode
                        assert state["runs"] == [] and state["queue"] == []
                        assert state["budget"]["attempt_count"] == base_attempts, state["budget"]
                        self.capture(shot)
                        return {"admission": body, "state_seq": body.get("state_seq")}

                    return run

                self.step(
                    "단독 '/웹': 선택만 바뀌고 실행·큐·모델 호출 0",
                    mode_only("/웹", "web", "웹 검색", "무비용_02_웹선택.png"),
                )

                def persisted_after_reload():
                    self.page.reload()
                    self.wait_ready()
                    self.expect_indicator("웹 검색")
                    assert self.api("/api/spike/snapshot")["selected_source_mode"] == "web"
                    return {}

                self.step("새로고침 후에도 '웹 검색' 선택 유지", persisted_after_reload)
                self.step(
                    "단독 '/디비': 데이터셋으로 복귀하고 실행·큐·모델 호출 0",
                    mode_only("/디비", "local", "데이터셋", "무비용_03_디비복귀.png"),
                )

                def conflict():
                    status, body = self.send("/웹 /DB 충돌 질문")
                    assert status == 422, (status, body)
                    assert body["error"]["code"] == "invalid_command_combination", body
                    assert (
                        self.page.get_by_test_id("command-input").input_value()
                        == "/웹 /DB 충돌 질문"
                    )
                    state = self.api("/api/spike/snapshot")
                    assert state["runs"] == [] and state["selected_source_mode"] == "local"
                    assert state["budget"]["attempt_count"] == base_attempts
                    self.page.get_by_test_id("command-input").fill("")
                    return {"error": body["error"]["code"], "draft_preserved": True}

                self.step("충돌하는 두 접두어는 422로 거절되고 입력 보존", conflict)
                self.page.get_by_role("button", name="알림 닫기").click()
                self.close_popup()

                def narrow():
                    self.capture("무비용_04_좁은화면.png", 390, 844)
                    self.capture("무비용_05_가로형.png", 844, 390)
                    return {}

                self.step("좁은·낮은 화면에서도 표시기가 입력·메뉴와 겹치지 않음", narrow)
            finally:
                browser.close()

        def clean_end():
            state = self.api("/api/spike/snapshot")
            assert state["runs"] == [] and state["budget"]["attempt_count"] == base_attempts
            db = db_fingerprint(*self.db_paths())
            events = [r for r in db["실행기록.sqlite3"]["events"]]
            return {
                "final_selected_source_mode": state["selected_source_mode"],
                "journal_events": events,
            }

        self.step("검사 뒤: 실행 0·호출 집계 불변", clean_end, must=False)

    # ---- research -------------------------------------------------------------------
    def phase_research(self, submit, verify_only, budget_before_from=None):
        initial = self.api("/api/spike/snapshot")
        base_budget = initial["budget"]
        if verify_only and budget_before_from is not None:
            # 이미 끝난 실행의 장부 증감은 원 실행 증거의 시작 값을 기준으로 다시 검증한다.
            base_budget = json.loads(budget_before_from.read_text(encoding="utf-8"))[
                "budget_before"
            ]
        self.proof["budget_before"] = base_budget
        if submit:
            assert not initial["runs"], "격리 서버에 이미 실행이 있어 추가로 전송하지 않습니다."
            assert initial["source_modes"] == ["local", "web"]
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            self.attach(browser)
            try:
                self.open_page()
                self.capture("웹연구_00_첫화면.png")
                run = self.submit_and_wait() if submit else self.latest_run()
                self.proof["run"] = {k: v for k, v in run.items() if k != "draft"}
                self.verify_run(run, base_budget)
                if run["status"] == "completed" and run["quality"]["passed"]:
                    self.ui_checks(run)
            finally:
                browser.close()

    def latest_run(self):
        state = self.api("/api/spike/snapshot")
        run = next(r for r in state["runs"] if r["run_id"] == state["current_run_id"])
        self.proof["budget_after_research"] = state["budget"]
        return run

    def submit_and_wait(self):
        page = self.page
        status, body = self.send("/웹 " + QUESTION)
        assert status == 202 and body["kind"] == "research", (status, body)
        assert body["source_mode"] == "web" and body["mode"] == "immediate", body
        self.proof["admission"] = body
        run_id = body["run_id"]
        print("SUBMITTED " + json.dumps({"run_id": run_id}), flush=True)
        page.wait_for_function("document.querySelector('[data-testid=command-input]').value===''")
        self.expect_indicator("웹 검색")
        self.proof["checks"].append(
            {
                "name": "화면 입력창의 '/웹 질문' 전송: 연구 접수·표시기 '웹 검색'",
                "status": "PASS",
                "admission": body,
            }
        )
        last, captured, lock_tested = None, False, False
        deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
        run = None
        while time.monotonic() < deadline:
            state = self.api("/api/spike/snapshot")
            run = next((r for r in state["runs"] if r["run_id"] == run_id), None)
            if run is None:
                page.wait_for_timeout(1000)
                continue
            usage = run.get("web_usage")
            marker = (run["status"], run["phase"], run["actual_model_requests"], str(usage))
            if marker != last:
                print("RESEARCH_STATE " + json.dumps(marker, ensure_ascii=False), flush=True)
                last = marker
            if run["status"] == "running" and run["phase"] == "researching":
                if not captured:
                    self.capture("웹연구_01_조사진행.png")
                    captured = True
                if not lock_tested:
                    self.step(
                        "진행 중 다른 자료 명령은 409로 거절되고 입력·실행이 보존됨",
                        self.lock_test,
                        must=False,
                    )
                    lock_tested = True
            if run["status"] != "running":
                break
            page.wait_for_timeout(1500)
        assert run is not None and run["status"] != "running", "제한 시간 안에 끝나지 않았습니다."
        self.proof["budget_after_research"] = self.api("/api/spike/snapshot")["budget"]
        return run

    def lock_test(self):
        before = self.api("/api/spike/snapshot")
        status, body = self.send("/디비 다른 질문")
        assert status == 409 and body["error"]["code"] == "source_mode_locked", (status, body)
        assert self.page.get_by_test_id("command-input").input_value() == "/디비 다른 질문"
        notice = self.page.locator(".composer-notice")
        notice.wait_for()
        assert "/큐" in notice.inner_text(), notice.inner_text()
        after = self.api("/api/spike/snapshot")
        assert len(after["runs"]) == len(before["runs"]) == 1 and after["queue"] == []
        assert after["runs"][0]["control_revision"] == before["runs"][0]["control_revision"]
        self.page.get_by_role("button", name="알림 닫기").click()
        self.page.get_by_test_id("command-input").fill("")
        return {"error": body["error"], "draft_preserved": True, "queue_unchanged": True}

    def verify_run(self, run, base_budget):
        api = self.api

        def outcome():
            assert run["status"] == "completed", {k: run.get(k) for k in ("status", "phase")}
            assert run["quality"]["passed"] is True, run["quality"]
            assert run["source_mode"] == "web" and run["mock_requests"] == 0
            assert 0 < run["actual_model_requests"] <= MODEL_REQUEST_CEILING
            quality = run["quality"]
            return {
                "run_id": run["run_id"],
                "report_id": run["report_id"],
                "actual_model_requests": run["actual_model_requests"],
                "web_usage": run.get("web_usage"),
                "research_stop_reason": run.get("research_stop_reason"),
                "quality_reasons": quality["reasons"],
                "quality_warnings": quality["warnings"],
                "metrics": quality["metrics"],
            }

        if not self.step(
            "실행 결과: 완료·필수 검사 통과·자료 web·모델 요청 상한 이내", outcome, must=False
        ):
            self.failure_evidence(run)
            return
        archive = api(f"/api/spike/runs/{run['run_id']}/archive")
        events = archive["events"]
        kinds = {}
        for event in events:
            kinds[event["type"]] = kinds.get(event["type"], 0) + 1
        self.proof["event_counts"] = dict(sorted(kinds.items()))

        def ledger():
            after = self.proof["budget_after_research"]
            delta = after["attempt_count"] - base_budget["attempt_count"]
            completed = kinds.get("model.completed", 0)
            assert delta == run["actual_model_requests"] == completed, (
                delta,
                run["actual_model_requests"],
                completed,
            )
            spent = after["spent_usd"] - base_budget["spent_usd"]
            assert after["unknown_count"] == 0 and after["reserved_usd"] == 0, after
            return {
                "ledger_attempt_delta": delta,
                "model_completed_events": completed,
                "estimated_cost_delta_usd": round(spent, 6),
                "tokens_in_delta": after["input_tokens"] - base_budget["input_tokens"],
                "tokens_out_delta": after["output_tokens"] - base_budget["output_tokens"],
                "budget_after": after,
            }

        self.step("모델 요청 수: 장부 증가=실행 기록=모델 완료 사건, 미정산 0", ledger, must=False)

        def web_events():
            usage = run["web_usage"]
            starts = [
                e for e in events if e["type"] == "tool.started" and e["input"].get("operation")
            ]
            searches = [e for e in starts if e["input"]["operation"] == "search"]
            fetches = [e for e in starts if e["input"]["operation"] == "fetch"]
            assert len(searches) == usage["search_attempts"] <= 16, usage
            assert len(fetches) == usage["fetch_attempts"] <= 24, usage
            requests = [e for e in events if e["type"] == "web.request.started"]
            ended = {
                e["output"]["web_request_id"]
                for e in events
                if e["type"] in ("web.request.completed", "web.request.failed")
            }
            assert len(requests) == usage["observed_http_requests"], (len(requests), usage)
            assert all(e["output"]["web_request_id"] in ended for e in requests)
            search_hosts = {
                re.match(r"https?://([^/]+)", e["output"]["url"]).group(1)
                for e in requests
                if e["output"]["operation"] == "search"
            }
            assert search_hosts <= {"search.yahoo.com"}, search_hosts
            failed = [e for e in events if e["type"] == "web.request.failed"]
            return {
                "logical_search_attempts": len(searches),
                "logical_fetch_attempts": len(fetches),
                "observed_http_requests": len(requests),
                "failed_http_requests": [
                    {
                        "operation": e["output"]["operation"],
                        "status_code": e["output"].get("status_code"),
                        "error_code": e["output"].get("error_code"),
                    }
                    for e in failed
                ],
                "tool_failed_events": [
                    e["error"].get("code") for e in events if e["type"] == "tool.failed"
                ],
                "search_hosts": sorted(search_hosts),
                "model_events_are_separate": kinds.get("model.completed", 0) != len(requests),
            }

        self.step(
            "웹 요청 사건: 논리 시도·관측 HTTP 요청·모델 호출이 서로 다른 사건으로 기록",
            web_events,
            must=False,
        )

        report = api("/api/spike/reports/" + run["report_id"])

        def report_checks():
            markdown = report["markdown"]
            assert report["source_mode"] == "web" and "## 출처 및 이용허락" in markdown
            assert "wikipedia.org" not in markdown and "CC BY-SA" not in markdown
            sources = report["sources"]
            ids = {s["source_id"] for s in sources}
            cited = set(re.findall(r"«([^»]+)»", markdown))
            assert cited and cited <= ids, sorted(cited - ids)
            assert all(s["published_at"] is None for s in sources)
            urls = [s.get("final_url") or s["url"] for s in sources]
            assert len(set(urls)) == len(urls) >= 2 and all(u.startswith("https://") for u in urls)
            sections = re.findall(r"^## \d+\. ", markdown, re.MULTILINE)
            body = markdown.split("\n\n## 출처 및 이용허락")[0]
            return {
                "report_sha256": report["sha256"],
                "characters": len(markdown),
                "body_characters": len(body),
                "sections": len(sections),
                "citations": len(re.findall(r"«([^»]+)»", markdown)),
                "distinct_cited_sources": len(cited),
                "sources": [
                    {
                        "source_id": s["source_id"],
                        "title": s["title"],
                        "url": s.get("final_url") or s["url"],
                        "text_sha256": s["text_sha256"],
                        "fetched_at": s["fetched_at"],
                    }
                    for s in sources
                ],
            }

        self.step(
            "보고서: 웹 출처 URL·인용 ID가 읽은 자료와 일치·위키 문구 없음",
            report_checks,
            must=False,
        )

        def sidecar():
            path = self.data_dir / "웹자료/자료.sqlite3"
            with closing(ro_connect(path)) as db:
                rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM web_sources")]
                reads = [json.loads(r[0]) for r in db.execute("SELECT body FROM web_reads")]
            ready = [r for r in rows if r["status"] == "ready"]
            for row in ready:
                assert sha256_bytes(row["text"].encode()) == row["text_sha256"], row["source_id"]
            by_status = {}
            for row in rows:
                by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            with closing(ro_connect(self.data_dir / "실행기록.sqlite3")) as db:
                calls = {r[0]: r[1] for r in db.execute("SELECT call_id,status FROM calls")}
            assert reads and all(calls.get(c["model_call_id"]) == "completed" for c in reads)
            return {
                "source_rows_by_status": by_status,
                "ready_text_hashes_verified": len(ready),
                "read_claims": len(reads),
                "claims_linked_to_completed_model_calls": len(reads),
                "failed_sources": [
                    {"url": r["url"], "error_code": r.get("error_code")}
                    for r in rows
                    if r["status"] == "failed"
                ],
            }

        self.step("출처 저장소: 본문 해시·읽기 증거가 성공한 모델 호출에 연결", sidecar, must=False)

    def failure_evidence(self, run):
        try:
            self.panel("실행/큐")
            self.capture("웹연구_실패_실행큐.png")
            self.close_popup()
            self.panel("사건 기록")
            self.capture("웹연구_실패_사건기록.png")
        except Exception as error:  # noqa: BLE001 -- 실패 화면 증거 수집이 원래 실패를 가리지 않게 한다.
            self.proof["failure_capture_error"] = f"{type(error).__name__}: {error}"[:300]
        archive = self.api(f"/api/spike/runs/{run['run_id']}/archive")
        self.proof["failure_events"] = [
            {k: e.get(k) for k in ("type", "actor", "status", "summary", "error")}
            for e in archive["events"]
            if e["status"] in ("failed", "superseded") or "failed" in e["type"]
        ][-12:]

    def ui_checks(self, run):
        page = self.page
        report = self.api("/api/spike/reports/" + run["report_id"])
        before = self.fingerprint()

        def clicks():
            page.set_viewport_size({"width": 1440, "height": 900})
            self.close_popup()
            # 실시간 스냅샷은 최근 사건 100건만 담는다. 웹 연구는 사건이 수백 건이라 기획 단계 사건이
            # 창 밖으로 밀려 기획 말풍선이 없을 수 있다. 있는 말풍선만 클릭하고 부재는 증거로 남긴다.
            names = [
                b.get_attribute("data-testid")
                for b in page.locator('[data-testid^="bubble-"]').all()
            ]
            snapshot = self.api("/api/spike/snapshot")
            facts = {
                "bubbles": names,
                "planner_bubble_visible": "bubble-planner" in names,
                "events_in_snapshot": len(snapshot["events"]),
                "events_has_more": snapshot["events_has_more"],
                "high_watermark_seq": snapshot["high_watermark_seq"],
            }
            targets = [n for n in names if n != "bubble-workstation"]
            assert targets, facts
            for name in targets[:2]:
                page.get_by_test_id(name).click()
                detail = page.get_by_test_id("event-detail")
                detail.wait_for()
                assert run["run_id"] in detail.inner_text(), (name, facts)
                if name == "bubble-planner":
                    assert "openai" in detail.inner_text()
                self.capture(f"웹연구_02_사건상세_{name}.png")
                self.close_popup()
            if "bubble-workstation" in names:
                page.get_by_test_id("bubble-workstation").click()
                page.get_by_test_id("event-detail").wait_for()
                self.close_popup()
            return facts

        self.step("NPC·생산 오브젝트 클릭의 실제 사건 상세", clicks, must=False)

        def viewer():
            page.get_by_test_id("paper-document").click()
            view = page.get_by_test_id("report-viewer")
            view.wait_for()
            assert view.locator("pre.markdown").inner_text() == report["markdown"]
            target = self.download(
                view.get_by_role("link", name="Markdown 다운로드", exact=True),
                "웹연구_보고서.md",
            )
            data = target.read_bytes()
            assert data == report["markdown"].encode("utf-8")
            assert sha256_bytes(data) == report["sha256"]
            self.capture("웹연구_03_보고서뷰어.png")
            self.capture("웹연구_04_좁은보고서.png", 390, 844)
            page.set_viewport_size({"width": 1440, "height": 900})
            self.close_popup()
            return {"download_bytes": len(data), "sha256": report["sha256"]}

        self.step("제출 문서 클릭·뷰어·Markdown 다운로드·파일 재열기 동일성", viewer, must=False)

        def usage_popup():
            self.panel("실행/큐")
            text = page.get_by_test_id("popup").inner_text()
            web = run["web_usage"]
            for label in (
                f"논리 검색 시도 {web['search_attempts']} / 16",
                f"본문 수집 시도 {web['fetch_attempts']} / 24",
                f"관측 HTTP 요청 {web['observed_http_requests']}",
                "웹 검색",
            ):
                assert label in text, (label, text[:900])
            self.capture("웹연구_05_사용량.png")
            self.close_popup()
            return {"labels_checked": True}

        self.step(
            "실행/큐 팝업: 웹 사용량(논리 시도·관측 요청)과 자료 표시", usage_popup, must=False
        )

        def history():
            self.panel("과거 기록")
            popup = page.get_by_test_id("popup")
            assert "웹 검색 ·" in popup.inner_text()
            popup.get_by_role("button", name="자세히 보기", exact=True).last.click()
            detail = page.get_by_test_id("popup").inner_text()
            assert "자료" in detail and "웹 검색" in detail and QUESTION in detail
            self.capture("웹연구_06_과거기록상세.png")
            self.close_popup()
            return {}

        self.step("과거 기록: 실행이 '웹 검색'으로 표시", history, must=False)

        def replay():
            page.get_by_test_id("timeline-toggle").click()
            open_replay_from_history(page, run["run_id"])
            slider = page.get_by_test_id("timeline-scrubber")
            slider.wait_for()
            seen = {}
            for key in ("Home", "End"):
                slider.focus()
                slider.press(key)
                page.wait_for_timeout(300)
                seen[key] = self.indicator()
            assert seen["End"] == "웹 검색", seen
            assert page.get_by_test_id("command-submit").is_disabled()
            self.capture("웹연구_07_재생.png")
            page.get_by_test_id("replay-next").click()
            page.get_by_test_id("replay-live").click()
            return {"indicator_at_home_and_end": seen}

        self.step("읽기 전용 재생: 시점별 표시기('웹 검색')와 입력 잠금", replay, must=False)

        def reload_persist():
            page.reload()
            self.wait_ready()
            self.expect_indicator("웹 검색")
            assert self.api("/api/spike/snapshot")["selected_source_mode"] == "web"
            return {}

        self.step("새로고침 후 선택('웹 검색') 유지", reload_persist, must=False)

        def unchanged():
            after = self.fingerprint()
            assert after == before, {"before": before, "after": after}
            return {"fingerprint": after["api"]}

        self.step("열람·재생·새로고침 동안 모델·검색·DB·장부 불변", unchanged, must=False)

        def exports():
            view = self.open_history_report(run["run_id"])
            facts = {}
            for fmt in ("html", "pdf"):
                link = page.get_by_test_id(f"export-{fmt}-download")
                if not link.is_visible():
                    page.get_by_test_id(f"export-{fmt}-create").click()
                    link.wait_for(timeout=90000)
                target = self.download(link, f"웹연구_보고서.{fmt}")
                facts[fmt] = {
                    "bytes": target.stat().st_size,
                    "sha256": sha256_bytes(target.read_bytes()),
                }
            html_text = (self.output / "웹연구_보고서.html").read_text(encoding="utf-8")
            assert report["sources"][0]["title"][:10] in html_text or "출처" in html_text
            assert "출처 및 이용허락" in html_text
            import pypdfium2

            pdf = pypdfium2.PdfDocument(str(self.output / "웹연구_보고서.pdf"))
            pages = len(pdf)
            last = pdf[pages - 1]
            last_text = last.get_textpage().get_text_range()
            assert pages >= 2 and "출처" in "".join(
                pdf[i].get_textpage().get_text_range() for i in range(pages)
            )
            last.render(scale=1.4).to_pil().save(str(self.output / "웹연구_PDF_마지막쪽.png"))
            facts["pdf_pages"] = pages
            facts["pdf_last_page_characters"] = len(last_text)
            view.wait_for()
            return facts

        self.step(
            "HTML·PDF 변환: 웹 보고서(긴 URL·출처 절)의 생성·다운로드·재열기", exports, must=False
        )

        def exports_readonly():
            after = self.fingerprint()
            assert after["db"] == before["db"], {"before": before["db"], "after": after["db"]}
            return {"db_unchanged_by_exports": True}

        self.step("변환 뒤에도 모델·검색·DB·장부 불변", exports_readonly, must=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("readonly", "zero", "research"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--budget-before-from",
        type=Path,
        help="--verify-only에서 장부 증감의 시작 값을 읽을 원 실행의 확인.json",
    )
    args = parser.parse_args()
    if args.phase == "research" and args.submit == args.verify_only:
        parser.error("research 단계는 --submit 또는 --verify-only 중 하나만 지정해야 합니다.")
    if args.submit and not args.verify_only and args.phase != "research":
        parser.error("--submit은 research 단계에서만 사용합니다.")
    check = Check(args.base_url, args.label, args.phase, args.data_dir, args.ledger)
    try:
        if args.phase == "readonly":
            check.phase_readonly()
        elif args.phase == "zero":
            check.phase_zero()
        else:
            check.phase_research(args.submit, args.verify_only, args.budget_before_from)
    except StepFailed:
        pass
    except Exception as error:  # noqa: BLE001 -- 예기치 못한 실패도 증거 파일에 남긴다.
        check.proof["checks"].append(
            {
                "name": "검사 진행 중 예외",
                "status": "FAIL",
                "error": f"{type(error).__name__}: {error}"[:700],
                "traceback": traceback.format_exc()[-1600:],
            }
        )
        print(traceback.format_exc(), flush=True)
    raise SystemExit(check.finish())


if __name__ == "__main__":
    main()
