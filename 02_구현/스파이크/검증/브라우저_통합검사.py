"""명시적 모델 대역만 사용해 실제 서버·Chromium·파일·재시작을 검사한다."""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import traceback
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
PYTHON = ROOT / ".venv/Scripts/python.exe"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def get_json(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.load(response)


def wait_state(base, condition, reason, timeout=12):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = get_json(base, "/api/spike/snapshot")
            if condition(last):
                return last
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise AssertionError(f"상태 대기 실패: {reason}; last={last}")


def current(state):
    return next(run for run in state["runs"] if run["run_id"] == state["current_run_id"])


def run_by_id(state, run_id):
    return next(run for run in state["runs"] if run["run_id"] == run_id)


def gates(state, *, stale=False):
    return [gate for gate in state["waiting"] if gate["is_stale"] is stale]


def main(label, skip_captures=False):
    output = SPIKE / "검증결과" / label
    output.mkdir(parents=True, exist_ok=True)
    case_dir = ROOT / ".cache/스파이크_통합검사" / uuid.uuid4().hex
    case_dir.mkdir(parents=True)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    proof = {
        "started_at": datetime.now(UTC).isoformat(),
        "base_url": base,
        "scope": "실제 HTTP·SSE·SQLite·Chromium과 모델 응답 대역. 실제 GPT 미호출.",
        "checks": [],
        "screenshots": [],
        "browser_errors": [],
        "external_requests": [],
    }
    worker = None
    stop_file = None
    log_handle = None
    browser = None
    page = None
    instance = 0

    def passed(name, **evidence):
        proof["checks"].append({"name": name, "status": "PASS", **evidence})
        print(f"PASS {name}", flush=True)

    def start_worker():
        nonlocal worker, stop_file, log_handle, instance
        instance += 1
        stop_file = case_dir / f"종료_{instance}.flag"
        log_handle = (output / f"서버_{instance}.log").open("w", encoding="utf-8")
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
                str(case_dir / "저장소"),
                "--stop-file",
                str(stop_file),
                "--port",
                str(port),
            ],
            cwd=SPIKE,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise AssertionError(f"시험 서버 조기 종료: {worker.returncode}")
            try:
                health = get_json(base, "/api/health")
                assert health["provider"] == "test-double" and health["actual_model_requests"] == 0
                return worker.pid
            except OSError:
                time.sleep(0.1)
        raise AssertionError("시험 서버 기동 시간 초과")

    def stop_worker():
        nonlocal worker, log_handle
        if worker is not None:
            stop_file.touch()
            code = worker.wait(timeout=20)
            assert code == 0, f"시험 작업자 종료 코드 {code}"
            worker = None
        if log_handle is not None:
            log_handle.close()
            log_handle = None

    def submit(text):
        page.get_by_test_id("command-input").fill(text)
        page.get_by_test_id("command-submit").click()
        page.wait_for_function("document.querySelector('[data-testid=command-input]').value === ''")

    def screenshot(name, width=1440, height=1000):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("document.fonts.ready")
        page.wait_for_selector('[data-testid="village"][data-render-ready="true"]')
        page.evaluate("window.scrollTo(0,0)")
        if not skip_captures:
            page.screenshot(path=str(output / name), full_page=True)
        metrics = page.evaluate("""() => ({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
          canvasCount:document.querySelectorAll('canvas').length,
          canvasRendering:getComputedStyle(document.querySelector('canvas')).imageRendering})""")
        proof["screenshots"].append(
            {"file": name, "captured": not skip_captures, "viewport": [width, height], **metrics}
        )
        assert metrics["scrollWidth"] <= width, f"가로 넘침: {name}: {metrics}"
        assert metrics["canvasCount"] == 1

    try:
        first_pid = start_worker()
        passed("실제 HTTP 서버 기동", launcher_pid=first_pid)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            proof["chromium"] = browser.version
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000}, accept_downloads=True
            )
            page = context.new_page()
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
            sse_responses = []
            page.on(
                "response",
                lambda response: (
                    sse_responses.append(response.url)
                    if response.headers.get("content-type", "").startswith("text/event-stream")
                    else None
                ),
            )
            page.goto(base + "/#/assets")
            page.wait_for_selector('[data-testid="village"][data-render-ready="true"]')
            initial_calls = get_json(base, "/api/spike/snapshot")["mock_requests"]
            page.get_by_role("button", name="이동 보기", exact=True).click()
            page.wait_for_selector('[data-testid="village"][data-phase="at-workstation"]')
            page.get_by_role("button", name="작업 보기", exact=True).click()
            page.wait_for_selector('[data-testid="village"][data-phase="worked"]')
            page.get_by_test_id("bubble-workstation").click(position={"x": 7, "y": 7})
            assert "LOCAL SYNTHETIC" in page.get_by_test_id("inspector").inner_text()
            assert "(클릭해서 더 자세히)" in page.get_by_test_id("bubble-workstation").inner_text()
            page.get_by_role("button", name="문서 운반", exact=True).click()
            page.wait_for_selector('[data-testid="village"][data-phase="submitted"]')
            page.get_by_test_id("paper-document").click()
            assert (
                "합성 종이를 제출 위치에 내려놓음" in page.get_by_test_id("inspector").inner_text()
            )
            timestamp_text = page.get_by_test_id("inspector").locator("dd").nth(1).inner_text()
            raw_timestamp = re.search(
                r"\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})", timestamp_text
            ).group()
            expected_kst = (
                datetime.fromisoformat(raw_timestamp)
                .astimezone(timezone(timedelta(hours=9)))
                .strftime("%H:%M:%S")
            )
            rendered_time = page.locator(".event-log time").first.inner_text()
            assert rendered_time == expected_kst, (
                f"한국 시간 표시: expected={expected_kst}, actual={rendered_time}"
            )
            passed(
                "저장 UTC 원본을 한국 시간으로 표시",
                original_utc=raw_timestamp,
                rendered_kst=rendered_time,
            )
            assert get_json(base, "/api/spike/snapshot")["mock_requests"] == initial_calls
            passed(
                "에셋 이동·작업·운반·전체 말풍선·종이 클릭",
                calls_before=initial_calls,
                calls_after=initial_calls,
            )
            screenshot("에셋_넓은화면.png")
            screenshot("에셋_휴대폭.png", 390, 844)
            page.set_viewport_size({"width": 1440, "height": 1000})

            submit("스파이크 A: 이전 요청을 대기시킨 상태로 유지합니다.")
            state_a = wait_state(base, lambda s: len(gates(s)) == 4, "A 네 조사 대역 대기")
            run_id = state_a["current_run_id"]
            submit("/큐 스파이크 C: B의 최종 제출 뒤에만 시작합니다.")
            wait_state(base, lambda s: len(s["queue"]) == 1, "C 큐 보관")
            question_b = (
                "스파이크 B: 새 지시와 긴 문자열의 반영을 확인합니다. "
                + "긴제목검사" * 22
                + " https://example.invalid/"
                + "테스트경로" * 25
            )
            submit(question_b)
            state_b = wait_state(
                base,
                lambda s: (
                    s["current_run_id"] == run_id
                    and current(s)["control_revision"] == 2
                    and len(current(s)["plan"]) == 4
                    and len(gates(s)) == 4
                    and len(gates(s, stale=True)) == 4
                ),
                "A 응답 해제 전 B의 계획·배정",
            )
            applied = [
                event
                for event in state_b["events"]
                if event["run_id"] == run_id
                and event["control_revision"] == 2
                and event["type"] == "instruction.applied"
            ]
            assert applied and len(state_b["queue"]) == 1
            page.wait_for_function(
                "document.querySelector('.run-summary')?.innerText.includes('버전 2')"
            )
            page.get_by_test_id("bubble-planner").click()
            page.wait_for_function(
                "document.querySelector('[data-testid=inspector]').getAttribute('aria-busy') === 'false'"
            )
            frozen = page.get_by_test_id("inspector").inner_text()
            assert question_b in frozen
            passed(
                "A 대기 중 같은 run의 B 계획·배정 저장",
                run_id=run_id,
                old_revision=1,
                new_revision=2,
                old_waits=len(gates(state_b, stale=True)),
                applied_seq=applied[-1]["seq"],
            )
            screenshot("제어_개입_넓은화면.png")
            screenshot("제어_개입_좁은창.png", 1100, 800)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="현재 1건 완료", exact=True).click()
            partial = wait_state(base, lambda s: len(gates(s)) == 3, "B 한 건 응답 완료")
            assert current(partial)["report_id"] is None and len(partial["queue"]) == 1
            assert page.get_by_test_id("inspector").inner_text() == frozen
            calls_before_reload = partial["mock_requests"]
            page.reload()
            page.wait_for_selector('[data-testid="village"][data-render-ready="true"]')
            restored = wait_state(base, lambda s: len(gates(s)) == 3, "새로고침 같은 실행 복원")
            assert (
                restored["current_run_id"] == run_id and current(restored)["control_revision"] == 2
            )
            assert restored["mock_requests"] == calls_before_reload
            passed(
                "한 조사 완료는 큐를 시작하지 않음·선택 사건 고정·새로고침 무재호출",
                mock_requests=calls_before_reload,
            )
            page.get_by_role("button", name="현재 모두 완료", exact=True).click()
            published = wait_state(
                base,
                lambda s: (
                    run_by_id(s, run_id)["status"] == "completed"
                    and s["current_run_id"] != run_id
                    and len(gates(s)) == 4
                ),
                "B 제출 뒤 C FIFO 시작",
            )
            report_id = run_by_id(published, run_id)["report_id"]
            report = get_json(base, f"/api/spike/reports/{report_id}")
            assert (
                report["control_revision"] == 2
                and question_b in report["markdown"]
                and len(report["markdown"]) >= 2000
            )
            submitted_seq = next(
                e["seq"]
                for e in published["events"]
                if e["run_id"] == run_id and e["type"] == "report.submitted"
            )
            next_seq = min(
                e["seq"] for e in published["events"] if e["run_id"] == published["current_run_id"]
            )
            assert submitted_seq < next_seq
            passed(
                "실제 최종 파일·제출 확정 후 다음 큐 시작",
                report_id=report_id,
                submitted_seq=submitted_seq,
                next_run_first_seq=next_seq,
            )
            page.get_by_role("button", name="이전 응답 돌려주기", exact=True).click()
            late = wait_state(base, lambda s: not gates(s, stale=True), "늦은 A 응답 보존·차단")
            assert run_by_id(late, run_id)["report_id"] == report_id
            assert get_json(base, f"/api/spike/reports/{report_id}")["sha256"] == report["sha256"]
            assert sum(e["type"] == "result.superseded" for e in late["events"]) >= 4
            passed(
                "최신 제출 후 늦은 A 결과가 원고·평가·정본을 덮지 않음",
                unchanged_sha256=report["sha256"],
            )
            page.get_by_test_id("paper-document").click()
            page.get_by_test_id("report-viewer").wait_for()
            assert report_id in page.get_by_test_id("report-viewer").inner_text()
            with page.expect_download() as pending_download:
                page.get_by_role("link", name="이 문서 Markdown 다운로드", exact=True).click()
            downloaded = output / "🧪 내려받은 스파이크 보고서.md"
            pending_download.value.save_as(str(downloaded))
            assert hashlib.sha256(downloaded.read_bytes()).hexdigest() == report["sha256"]
            assert downloaded.read_text(encoding="utf-8") == report["markdown"]
            passed(
                "운반 뒤 같은 보고서 열기·다운로드·파일 재열기",
                filename=pending_download.value.suggested_filename,
                bytes=downloaded.stat().st_size,
                sha256=report["sha256"],
            )
            screenshot("제어_보고서_열람.png")
            page.get_by_role("button", name="문서 접기", exact=True).click()

            submit("/큐 스파이크 D: 실패해도 잃지 않는 대기 지시입니다.")
            wait_state(base, lambda s: len(s["queue"]) == 1, "실패 검사 대기 큐")
            page.get_by_role("button", name="실패 응답", exact=True).click()
            failed = wait_state(
                base,
                lambda s: current(s)["status"] == "failed" and s["queue_paused"],
                "실패 보존·정지",
            )
            assert len(failed["queue"]) == 1 and current(failed)["report_id"] is None
            passed("실제 오류 경로에서 큐 보존·정지", reason=failed["queue_pause_reason"])
            page.wait_for_function(
                "document.querySelector('.run-summary')?.innerText.includes('failed')"
            )
            publisher_status = page.locator('.role-strip button[title="publisher"]').inner_text()
            assert "completed" not in publisher_status, (
                f"다른 실행의 제출 완료가 현재 역할에 섞임: {publisher_status}"
            )
            assert "이전 제출 문서" in page.locator(".scene-status").inner_text()
            passed("현재 역할·버전과 이전 제출 문서의 표시를 분리", publisher=publisher_status)
            screenshot("제어_실패_휴대폭.png", 390, 844)
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("button", name="큐 계속", exact=True).click()
            wait_state(
                base, lambda s: not s["queue_paused"] and len(gates(s)) == 4, "사용자 큐 계속"
            )
            submit("/큐 스파이크 E: 품질 불합격 뒤에도 보관합니다.")
            wait_state(base, lambda s: len(s["queue"]) == 1, "품질 검사 대기 큐")
            page.get_by_role("button", name="불합격 초안", exact=True).click()
            blocked = wait_state(
                base,
                lambda s: current(s)["status"] == "blocked" and s["queue_paused"],
                "품질 불합격",
            )
            assert (
                current(blocked)["draft"]
                and current(blocked)["report_id"] is None
                and len(blocked["queue"]) == 1
            )
            passed(
                "불합격 초안 보존·최종 제출 없음·큐 정지",
                reasons=current(blocked)["quality"]["reasons"],
            )
            submit("스파이크 F: 일반 지시는 큐 일시정지를 해제하지 않습니다.")
            ordinary = wait_state(
                base,
                lambda s: len(gates(s)) == 4 and current(s)["status"] == "running",
                "정지 중 일반 지시",
            )
            assert ordinary["queue_paused"] and len(ordinary["queue"]) == 1
            page.get_by_role("button", name="현재 모두 완료", exact=True).click()
            ordinary_done = wait_state(
                base, lambda s: current(s)["status"] == "completed", "정지 중 일반 지시 완료"
            )
            assert ordinary_done["queue_paused"] and len(ordinary_done["queue"]) == 1
            passed("일반 지시 완료가 기존 큐를 임의 재개하지 않음")
            submit("스파이크 G: 실제 서버 프로세스 재시작 전에 응답을 대기합니다.")
            before_restart = wait_state(
                base,
                lambda s: len(gates(s)) == 4 and current(s)["status"] == "running",
                "재시작 대기",
            )
            restart_run_id = before_restart["current_run_id"]
            restart_calls = before_restart["mock_requests"]
            assert sse_responses, "실제 EventSource 응답이 관측되지 않음"
            assert not proof["browser_errors"], proof["browser_errors"]
            assert not proof["external_requests"], proof["external_requests"]
            passed(
                "한 EventSource로 실제 SSE 사건 수신·브라우저 오류 없음",
                sse_urls=sorted(set(sse_responses)),
            )
            browser.close()
            browser = None
            stop_worker()
            second_pid = start_worker()
            after_restart = get_json(base, "/api/spike/snapshot")
            assert after_restart["current_run_id"] == restart_run_id
            assert (
                current(after_restart)["status"] == "interrupted" and after_restart["queue_paused"]
            )
            assert (
                len(after_restart["queue"]) == 1 and after_restart["mock_requests"] == restart_calls
            )
            assert not after_restart["waiting"] and after_restart["actual_model_requests"] == 0
            assert get_json(base, f"/api/spike/reports/{report_id}")["sha256"] == report["sha256"]
            passed(
                "실제 서버 프로세스 재시작·DB 복원·모델 대역 무재실행",
                launcher_pids=[first_pid, second_pid],
                restored_status=current(after_restart)["status"],
                mock_requests=restart_calls,
            )
        proof["status"] = "PASS"
    except Exception as error:
        proof["status"] = "FAIL"
        proof["error"] = str(error)
        proof["traceback"] = traceback.format_exc()
        if page is not None and browser is not None:
            try:
                page.screenshot(path=str(output / "실패_화면.png"), full_page=True)
                proof["failure_dom_text"] = page.inner_text("body")
            except (PlaywrightError, OSError) as capture_error:
                proof["failure_capture_error"] = str(capture_error)
        raise
    finally:
        try:
            try:
                if browser is not None:
                    browser.close()
            except PlaywrightError as cleanup_error:
                # 실패가 with 경계를 벗어나면 Playwright가 이미 닫혀 있을 수 있다.
                proof.setdefault("cleanup_notes", []).append(str(cleanup_error))
        finally:
            try:
                stop_worker()
            finally:
                proof["finished_at"] = datetime.now(UTC).isoformat()
                (output / "통합_실제실행.json").write_text(
                    json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(
                    json.dumps(
                        {
                            "status": proof.get("status"),
                            "checks": len(proof["checks"]),
                            "proof": str(output / "통합_실제실행.json"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="첫검사")
    parser.add_argument("--skip-captures", action="store_true")
    args = parser.parse_args()
    main(args.label, args.skip_captures)
