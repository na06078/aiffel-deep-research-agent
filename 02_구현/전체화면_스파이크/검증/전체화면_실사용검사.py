"""전체 화면 마을의 실제 viewport·팝업·입력과 로컬 실행 경로를 검사한다."""

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
import traceback
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
PYTHON = ROOT / ".venv/Scripts/python.exe"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def request_json(base, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=body)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def current(state):
    return next(run for run in state["runs"] if run["run_id"] == state["current_run_id"])


def waits(state, stale=False):
    return [gate for gate in state["waiting"] if gate["is_stale"] is stale]


def wait_state(base, predicate, reason):
    deadline = time.monotonic() + 15
    last = None
    while time.monotonic() < deadline:
        last = request_json(base, "/api/spike/snapshot")
        if predicate(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"상태 대기 실패: {reason}; last={last}")


def main(label):
    output = SPIKE / "검증결과" / label
    output.mkdir(parents=True, exist_ok=True)
    case = ROOT / ".cache/전체화면_통합검사" / uuid.uuid4().hex
    case.mkdir(parents=True)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    proof = {
        "started_at": datetime.now(UTC).isoformat(),
        "base_url": base,
        "checks": [],
        "captures": [],
        "browser_errors": [],
        "external_requests": [],
        "scope": "실제 전체 viewport/팝업/입력·HTTP/SSE/SQLite/파일·모델 응답 대역",
    }
    worker = None
    stop_file = None
    log = None
    browser = None
    page = None
    instance = 0

    def passed(name, **facts):
        proof["checks"].append({"name": name, "status": "PASS", **facts})
        print("PASS " + name, flush=True)

    def start_worker():
        nonlocal worker, stop_file, log, instance
        instance += 1
        stop_file = case / f"종료_{instance}.flag"
        log = (output / f"서버_{instance}.log").open("w", encoding="utf-8")
        env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        }
        worker = subprocess.Popen(
            [
                str(PYTHON),
                str(SPIKE / "검증/서버_시험작업자.py"),
                "--data-dir",
                str(case / "기록"),
                "--stop-file",
                str(stop_file),
                "--port",
                str(port),
            ],
            cwd=SPIKE,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise AssertionError(f"시험 서버 조기 종료: {worker.returncode}")
            try:
                health = request_json(base, "/api/health")
                assert health["provider"] == "test-double" and health["actual_model_requests"] == 0
                return worker.pid
            except OSError:
                time.sleep(0.1)
        raise AssertionError("시험 서버 기동 시간 초과")

    def stop_worker():
        nonlocal worker, log
        if worker is not None:
            stop_file.touch()
            assert worker.wait(timeout=20) == 0
            worker = None
        if log is not None:
            log.close()
            log = None

    def geometry(name, same_canvas=True):
        result = page.evaluate("""() => {
          const world=document.querySelector('[data-testid=world]');
          const canvas=world?.querySelector('canvas');
          const rect=world?.getBoundingClientRect(); const cr=canvas?.getBoundingClientRect();
          return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,
            scrollHeight:document.documentElement.scrollHeight,scrollX,scrollY,
            world:rect?{x:rect.x,y:rect.y,width:rect.width,height:rect.height}:null,
            canvas:cr?{x:cr.x,y:cr.y,width:cr.width,height:cr.height}:null,
            canvasCount:document.querySelectorAll('canvas').length,
            sameCanvas:window.__verifiedCanvas===canvas};
        }""")
        assert result["canvasCount"] == 1, result
        for key in ("world", "canvas"):
            rect = result[key]
            assert rect and abs(rect["x"]) <= 1 and abs(rect["y"]) <= 1, (name, result)
            assert abs(rect["width"] - result["width"]) <= 1, (name, result)
            assert abs(rect["height"] - result["height"]) <= 1, (name, result)
        assert (
            result["scrollWidth"] <= result["width"]
            and result["scrollHeight"] <= result["height"] + 1
        )
        assert result["scrollX"] == result["scrollY"] == 0
        if same_canvas:
            assert result["sameCanvas"], name
        return result

    def hit(locator):
        locator.wait_for(state="visible")
        assert locator.evaluate("""e => {const r=e.getBoundingClientRect();
          const x=r.left+r.width/2,y=r.top+r.height/2;
          if(x<0||y<0||x>=innerWidth||y>=innerHeight)return false;
          const top=document.elementFromPoint(x,y);return e===top||e.contains(top);} """), (
            locator.inner_text()
        )

    def close_popup():
        close = page.get_by_test_id("popup-close")
        if close.count() and close.is_visible():
            hit(close)
            close.click()
            page.get_by_test_id("popup").wait_for(state="hidden")
        drawer = page.get_by_test_id("drawer")
        if drawer.count() and drawer.is_visible():
            page.keyboard.press("Escape")

    def panel(name):
        close_popup()
        page.get_by_test_id("menu-toggle").click()
        page.get_by_test_id("drawer").wait_for()
        geometry("드로어")
        page.get_by_test_id("drawer").get_by_role("button", name=name, exact=True).click()
        page.get_by_test_id("popup").wait_for()
        geometry("팝업 " + name)
        hit(page.get_by_test_id("command-input"))

    def control(name):
        panel("제어 도구")
        button = page.get_by_test_id("popup").get_by_role("button", name=name, exact=True)
        button.scroll_into_view_if_needed()
        hit(button)
        button.click()
        close_popup()

    def submit(text):
        hit(page.get_by_test_id("command-input"))
        page.get_by_test_id("command-input").fill(text)
        hit(page.get_by_test_id("command-submit"))
        page.get_by_test_id("command-submit").click()
        page.wait_for_function("document.querySelector('[data-testid=command-input]').value==='' ")

    def capture(name, width=1440, height=900):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("document.fonts.ready")
        page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
        page.wait_for_function("""() => {const w=document.querySelector('[data-testid=world]');
          return Math.abs(Number(w?.dataset.layoutWidth)-innerWidth)<4 && Math.abs(Number(w?.dataset.layoutHeight)-innerHeight)<4;}""")
        metrics = geometry(name)
        hit(page.get_by_test_id("command-input"))
        # 반드시 viewport만 저장한다. 긴 문서 전체 캡처로 화면 배치를 숨기지 않는다.
        page.screenshot(path=str(output / name), full_page=False)
        proof["captures"].append({"file": name, **metrics})

    try:
        pid1 = start_worker()
        passed("실제 새 경로 HTTP 서버", pid=pid1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            proof["chromium"] = browser.version
            page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
            page.on("pageerror", lambda error: proof["browser_errors"].append(str(error)))
            page.on(
                "request",
                lambda request: (
                    proof["external_requests"].append(request.url)
                    if request.url.startswith(("http:", "https:"))
                    and not request.url.startswith(base + "/")
                    else None
                ),
            )
            streams = []
            page.on(
                "response",
                lambda response: (
                    streams.append(response.url)
                    if response.headers.get("content-type", "").startswith("text/event-stream")
                    else None
                ),
            )
            page.goto(base + "/#/assets")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.evaluate("window.__verifiedCanvas=document.querySelector('canvas')")
            assert not page.get_by_test_id("popup").is_visible()
            assert not page.get_by_test_id("drawer").is_visible()
            capture("마을_첫화면.png")
            capture("마을_넓은화면.png", 1920, 1080)
            capture("마을_좁은화면.png", 390, 844)
            page.set_viewport_size({"width": 1440, "height": 900})
            passed("첫 viewport 전체 마을·여백/바탕스크롤 없음·동일 canvas")
            for action, phase in [
                ("이동 보기", "at-workstation"),
                ("작업 보기", "worked"),
                ("문서 운반", "submitted"),
            ]:
                panel("장면 도구")
                page.get_by_test_id("popup").get_by_role("button", name=action, exact=True).click()
                close_popup()
                page.wait_for_selector(f'[data-testid="world"][data-phase="{phase}"]')
                geometry(action)
            assert request_json(base, "/api/spike/snapshot")["mock_requests"] == 0
            page.get_by_test_id("bubble-workstation").click(position={"x": 7, "y": 7})
            page.get_by_test_id("popup").wait_for()
            page.get_by_test_id("command-input").fill("아직 보내지 않은 지시")
            capture("장면_사건팝업.png")
            close_popup()
            assert page.get_by_test_id("command-input").input_value() == "아직 보내지 않은 지시"
            passed("장면 메뉴·이동/작업/운반·상세 팝업·초안 보존·대역 무호출")
            page.get_by_test_id("command-input").fill("")
            page.goto(base + "/#/control")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.evaluate("window.__verifiedCanvas=document.querySelector('canvas')")
            a = "전체 화면 A: 이전 응답은 기다리게 합니다."
            submit(a)
            state = wait_state(base, lambda s: len(waits(s)) == 4, "A 조사 대기")
            run_id = state["current_run_id"]
            submit("/큐 전체 화면 C: 최종 제출 뒤 시작합니다.")
            wait_state(base, lambda s: len(s["queue"]) == 1, "큐 보관")
            page.get_by_test_id("bubble-planner").click()
            page.get_by_test_id("popup").wait_for()
            hit(page.get_by_test_id("command-input"))
            b = (
                "전체 화면 B: 팝업을 열어 둔 채 지시를 반영합니다. "
                + "긴제목" * 30
                + " https://example.invalid/"
                + "시험경로" * 25
            )
            submit(b)
            state = wait_state(
                base,
                lambda s: (
                    current(s)["control_revision"] == 2
                    and len(waits(s)) == 4
                    and len(waits(s, True)) == 4
                ),
                "이전 대기 중 B 배정",
            )
            assert state["current_run_id"] == run_id and len(current(state)["plan"]) == 4
            assert any(
                e["type"] == "instruction.applied" and e["control_revision"] == 2
                for e in state["events"]
            )
            close_popup()
            page.get_by_test_id("bubble-planner").click()
            page.get_by_test_id("event-detail").wait_for()
            page.wait_for_function(
                "document.querySelector('[data-testid=event-detail]')?.innerText.includes('전체 화면 B')"
            )
            frozen = page.get_by_test_id("event-detail").inner_text()
            capture("실행_상세팝업.png")
            one = waits(state)[0]["gate_id"]
            request_json(base, "/api/spike/releases", {"gate_ids": [one], "outcome": "success"})
            partial = wait_state(base, lambda s: len(waits(s)) == 3, "한 조사 완료")
            assert len(partial["queue"]) == 1 and current(partial)["report_id"] is None
            assert page.get_by_test_id("event-detail").inner_text() == frozen
            calls = partial["mock_requests"]
            page.reload()
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.evaluate("window.__verifiedCanvas=document.querySelector('canvas')")
            restored = request_json(base, "/api/spike/snapshot")
            assert restored["current_run_id"] == run_id and restored["mock_requests"] == calls
            passed("실제 입력·팝업 중 개입·새 계획 선반영·선택 고정·새로고침 무재호출")
            control("현재 모두 완료")
            state = wait_state(
                base,
                lambda s: (
                    next(r for r in s["runs"] if r["run_id"] == run_id)["status"] == "completed"
                    and s["current_run_id"] != run_id
                    and len(waits(s)) == 4
                ),
                "제출 뒤 C 시작",
            )
            report_id = next(r for r in state["runs"] if r["run_id"] == run_id)["report_id"]
            report = request_json(base, f"/api/spike/reports/{report_id}")
            assert b in report["markdown"] and report["control_revision"] == 2
            submitted = next(
                e["seq"]
                for e in state["events"]
                if e["run_id"] == run_id and e["type"] == "report.submitted"
            )
            next_first = min(
                e["seq"] for e in state["events"] if e["run_id"] == state["current_run_id"]
            )
            assert submitted < next_first
            control("이전 응답 돌려주기")
            wait_state(base, lambda s: not waits(s, True), "늦은 A 적용 차단")
            assert (
                request_json(base, f"/api/spike/reports/{report_id}")["sha256"] == report["sha256"]
            )
            capture("마을_실행중.png")
            passed("전체 제출 후 FIFO·이전 결과의 원고/평가/정본 적용 차단")
            page.get_by_test_id("paper-document").wait_for()
            page.get_by_test_id("paper-document").click()
            page.get_by_test_id("report-viewer").wait_for()
            assert report_id in page.get_by_test_id("report-viewer").inner_text()
            page.get_by_test_id("command-input").fill("보고서 팝업 중 보존할 지시")
            hit(page.get_by_test_id("command-input"))
            link = (
                page.get_by_test_id("report-viewer")
                .get_by_role("link")
                .filter(has_text="다운로드")
                .first
            )
            link.scroll_into_view_if_needed()
            hit(link)
            with page.expect_download() as pending:
                link.click()
            downloaded = output / "🧪 전체화면 검증 보고서.md"
            pending.value.save_as(str(downloaded))
            assert hashlib.sha256(downloaded.read_bytes()).hexdigest() == report["sha256"]
            assert downloaded.read_text(encoding="utf-8") == report["markdown"]
            capture("보고서_팝업.png")
            close_popup()
            assert (
                page.get_by_test_id("command-input").input_value() == "보고서 팝업 중 보존할 지시"
            )
            page.get_by_test_id("command-input").fill("")
            passed("이전 제출 문서의 팝업·입력 접근·실제 다운로드 재열기", sha256=report["sha256"])
            panel("과거 기록")
            page.get_by_test_id("popup").get_by_role(
                "button", name="자세히 보기", exact=True
            ).first.click()
            geometry("과거 상세")
            assert page.get_by_test_id("popup").count() == 1
            page.get_by_test_id("popup").get_by_role("button").filter(has_text="목록").first.click()
            close_popup()
            passed("햄버거·과거 기록·같은 중앙 창 상세/목록 복귀")
            submit("/큐 전체 화면 D: 실패 뒤 보존할 지시")
            wait_state(base, lambda s: len(s["queue"]) == 1, "실패 전 대기열")
            control("실패 응답")
            failure = wait_state(
                base,
                lambda s: current(s)["status"] == "failed" and s["queue_paused"],
                "실패/큐 정지",
            )
            assert len(failure["queue"]) == 1
            panel("실행/큐")
            capture("실패_큐팝업_좁은화면.png", 390, 844)
            resume = page.get_by_test_id("popup").get_by_role("button", name="큐 계속", exact=True)
            resume.scroll_into_view_if_needed()
            hit(resume)
            resume.click()
            close_popup()
            page.set_viewport_size({"width": 1440, "height": 900})
            wait_state(
                base, lambda s: not s["queue_paused"] and len(waits(s)) == 4, "사용자 큐 재개"
            )
            submit("/큐 전체 화면 E: 불합격 뒤 보존")
            wait_state(base, lambda s: len(s["queue"]) == 1, "불합격 전 큐")
            control("불합격 초안")
            blocked = wait_state(base, lambda s: current(s)["status"] == "blocked", "불합격 차단")
            assert (
                current(blocked)["draft"]
                and current(blocked)["report_id"] is None
                and blocked["queue_paused"]
            )
            submit("전체 화면 F: 정지 중 일반 지시")
            normal = wait_state(
                base,
                lambda s: current(s)["status"] == "running" and len(waits(s)) == 4,
                "정지 중 일반 지시",
            )
            assert normal["queue_paused"]
            control("현재 모두 완료")
            done = wait_state(base, lambda s: current(s)["status"] == "completed", "일반 지시 완료")
            assert done["queue_paused"] and len(done["queue"]) == 1
            passed("실패/불합격 보존·명시적 큐 계속·일반 지시가 기존 정지를 풀지 않음")
            submit("전체 화면 G: 재시작 전에 대기")
            before = wait_state(
                base,
                lambda s: current(s)["status"] == "running" and len(waits(s)) == 4,
                "재시작 준비",
            )
            restart_id = before["current_run_id"]
            assert streams and not proof["browser_errors"] and not proof["external_requests"]
            browser.close()
            browser = None
            stop_worker()
            pid2 = start_worker()
            after = request_json(base, "/api/spike/snapshot")
            assert (
                after["current_run_id"] == restart_id and current(after)["status"] == "interrupted"
            )
            assert after["queue_paused"] and len(after["queue"]) == 1 and not after["waiting"]
            assert (
                after["mock_requests"] == before["mock_requests"]
                and after["actual_model_requests"] == 0
            )
            passed("실제 서버 프로세스 재시작·SSE·DB 복원·대역 무재실행", pids=[pid1, pid2])
        proof["status"] = "PASS"
    except Exception as error:
        proof["status"] = "FAIL"
        proof["error"] = str(error)
        proof["traceback"] = traceback.format_exc()
        raise
    finally:
        try:
            try:
                if browser is not None:
                    browser.close()
            except PlaywrightError as error:
                proof["browser_cleanup_note"] = str(error)
        finally:
            try:
                stop_worker()
            finally:
                proof["finished_at"] = datetime.now(UTC).isoformat()
                (output / "전체화면_실제실행.json").write_text(
                    json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(
                    json.dumps(
                        {
                            "status": proof.get("status"),
                            "checks": len(proof["checks"]),
                            "output": str(output),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="첫검사")
    main(parser.parse_args().label)
