"""실제 HTTP/SQLite/Chromium의 시간 드로어와 읽기 전용 재생을 검사한다."""

import argparse
import hashlib
import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from 화면도우미 import open_replay_from_history

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
PYTHON = ROOT / ".venv/Scripts/python.exe"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")
module_spec = importlib.util.spec_from_file_location(
    "spike_checks", SPIKE / "검증/전체화면_실사용검사.py"
)
checks = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(checks)
request_json, wait_state, current, waits = (
    checks.request_json,
    checks.wait_state,
    checks.current,
    checks.waits,
)


def db_digest(path):
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
        tables = sorted(
            row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        )
        rows = {}
        for table in tables:
            safe = table.replace('"', '""')
            rows[table] = sorted(
                [list(row) for row in db.execute(f'SELECT * FROM "{safe}"')], key=repr
            )
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main(label):
    out = SPIKE / "검증결과" / label
    out.mkdir(parents=True, exist_ok=True)
    data = ROOT / ".cache/타임라인_통합검사" / uuid.uuid4().hex
    data.mkdir(parents=True)
    stop = data / "종료.flag"
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    proof = {
        "status": "RUNNING",
        "checks": [],
        "captures": [],
        "errors": [],
        "posts_during_replay": [],
        "external_requests": [],
    }
    worker = browser = None
    replay_window = {"active": False}
    log = (out / "서버.log").open("w", encoding="utf-8")

    def passed(name, **facts):
        proof["checks"].append({"name": name, "status": "PASS", **facts})
        print("PASS " + name, flush=True)

    try:
        env = {
            **os.environ,
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "PYTHONIOENCODING": "utf-8",
        }
        worker = subprocess.Popen(
            [
                str(PYTHON),
                str(SPIKE / "검증/서버_시험작업자.py"),
                "--data-dir",
                str(data / "기록"),
                "--stop-file",
                str(stop),
                "--port",
                str(port),
            ],
            cwd=SPIKE,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 20
        while True:
            assert worker.poll() is None, "시험 서버 조기 종료"
            try:
                health = request_json(base, "/api/health")
                break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(0.1)
        assert health["actual_model_requests"] == 0
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: proof["errors"].append(str(e)))

            def note_request(request):
                if request.url.startswith(("http:", "https:")) and not request.url.startswith(
                    base + "/"
                ):
                    proof["external_requests"].append(request.url)
                if replay_window["active"] and request.method not in ["GET", "HEAD", "OPTIONS"]:
                    proof["posts_during_replay"].append(
                        {"method": request.method, "url": request.url}
                    )

            page.on("request", note_request)
            page.goto(base + "/#/control")
            page.wait_for_selector("[data-testid=world][data-render-ready=true]")
            page.evaluate('window.__timelineCanvas=document.querySelector("canvas")')

            def geometry(name):
                metrics = page.evaluate("""() => {const w=document.querySelector('[data-testid=world]').getBoundingClientRect();
                  const c=document.querySelector('canvas').getBoundingClientRect();return {width:innerWidth,height:innerHeight,
                  world:{x:w.x,y:w.y,width:w.width,height:w.height},canvas:{x:c.x,y:c.y,width:c.width,height:c.height},
                  canvasCount:document.querySelectorAll('canvas').length,sameCanvas:window.__timelineCanvas===document.querySelector('canvas'),
                  scrollX,scrollY,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight};}""")
                expected = {"x": 0, "y": 0, "width": metrics["width"], "height": metrics["height"]}
                assert metrics["world"] == metrics["canvas"] == expected, (name, metrics)
                assert metrics["canvasCount"] == 1 and metrics["sameCanvas"]
                assert (
                    metrics["scrollX"] == metrics["scrollY"] == 0
                    and metrics["scrollWidth"] == metrics["width"]
                    and metrics["scrollHeight"] <= metrics["height"] + 1
                )
                return metrics

            def hit(locator):
                locator.wait_for(state="visible")
                assert locator.evaluate("""e=>{const r=e.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
                  const t=document.elementFromPoint(x,y);return x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&(t===e||e.contains(t));}""")

            def capture(name, width=1440, height=900):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_function("""() => {const w=document.querySelector('[data-testid=world]');
                  return Math.abs(Number(w?.dataset.layoutWidth)-innerWidth)<4&&Math.abs(Number(w?.dataset.layoutHeight)-innerHeight)<4;}""")
                hit(page.get_by_test_id("command-input"))
                hit(page.get_by_test_id("timeline-toggle"))
                page.screenshot(path=str(out / name), full_page=False)
                proof["captures"].append({"file": name, **geometry(name)})

            def close_popup():
                locator = page.get_by_test_id("popup-close")
                if locator.count() and locator.is_visible():
                    locator.click()
                drawer = page.get_by_test_id("drawer")
                if drawer.count() and drawer.is_visible():
                    page.keyboard.press("Escape")

            def control(name):
                close_popup()
                page.get_by_test_id("menu-toggle").click()
                page.get_by_test_id("drawer").get_by_role(
                    "button", name="제어 도구", exact=True
                ).click()
                button = page.get_by_test_id("popup").get_by_role("button", name=name, exact=True)
                button.scroll_into_view_if_needed()
                hit(button)
                button.click()
                close_popup()

            def submit(text):
                hit(page.get_by_test_id("command-input"))
                page.get_by_test_id("command-input").fill(text)
                page.get_by_test_id("command-submit").click()
                page.wait_for_function(
                    'document.querySelector("[data-testid=command-input]").value===""'
                )

            def drawer_open():
                drawer = page.get_by_test_id("timeline-drawer")
                if not drawer.count() or not drawer.is_visible():
                    page.get_by_test_id("timeline-toggle").click()
                drawer.wait_for(state="visible")
                geometry("타임라인 열기")
                hit(page.get_by_test_id("command-input"))

            def seek(value):
                slider = page.get_by_test_id("timeline-scrubber")
                slider.scroll_into_view_if_needed()
                # 실제 HTML range의 native setter와 input/change 경계를 사용해 정확한 시점을 검사한다.
                slider.evaluate(
                    """(e,value)=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,String(value));
                  e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}""",
                    value,
                )
                page.wait_for_function(
                    '(value)=>Math.abs(Number(document.querySelector("[data-testid=world]").dataset.sceneTime)-value)<2',
                    arg=float(value),
                )

            def positions():
                return page.locator('[data-testid^="npc-"]').evaluate_all(
                    "es=>es.map(e=>({id:e.dataset.testid,x:e.style.left,y:e.style.top,status:e.dataset.status}))"
                )

            capture("마을_새첫화면.png")
            page.get_by_test_id("timeline-toggle").click()
            page.get_by_test_id("timeline-drawer").wait_for()
            capture("시간창_빈상태.png")
            page.get_by_test_id("timeline-toggle").click()
            assert page.get_by_test_id("timeline-toggle").is_visible()
            passed("전체 마을 영역·입력 아래 접이식 시간창·동일 canvas")
            a = "시간 시험 A: 마을 숲과 도서관의 연결"
            b = "시간 시험 B: 과거 지시와 현재 지시를 구분"
            submit(a)
            state = wait_state(base, lambda s: len(waits(s)) == 4, "A 네 조사관 대기")
            run_id = state["current_run_id"]
            drawer_open()
            time.sleep(0.6)
            capture("시간창_동시작업.png")
            submit(b)
            state = wait_state(
                base,
                lambda s: current(s)["control_revision"] == 2 and len(waits(s)) == 4,
                "B 새 버전",
            )
            time.sleep(0.6)
            control("현재 모두 완료")
            wait_state(base, lambda s: current(s)["status"] == "completed", "B 최종 제출")
            control("이전 응답 돌려주기")
            wait_state(base, lambda s: not waits(s, True), "A 늦은 응답 기록")
            deadline = time.monotonic() + 10
            while True:
                archive = request_json(base, f"/api/spike/runs/{run_id}/archive")
                if not any(s["status"] == "running" for s in archive["spans"]):
                    break
                assert time.monotonic() < deadline
                time.sleep(0.1)
            assert (
                archive["truncated"] is False
                and archive["has_measured_spans"]
                and archive["provider"] == "test-double"
            )
            assert len(archive["revisions"]) >= 2
            assert any(
                s["kind"] == "model_wait"
                and s["duration_ms"] is not None
                and s["duration_ms"] >= 400
                for s in archive["spans"]
            )
            for span in archive["spans"]:
                assert span["run_id"] == run_id and (
                    span["duration_ms"] is None or span["duration_ms"] >= 0
                )
                for event_id in span["event_ids"]:
                    assert any(e["event_id"] == event_id for e in archive["events"])
            passed(
                "실제 구간 측정·병렬 대기·실행/버전별 전체 아카이브",
                spans=len(archive["spans"]),
                events=len(archive["events"]),
            )
            drawer_open()
            open_replay_from_history(page, run_id)
            page.get_by_test_id("timeline-scrubber").wait_for()
            terminal = request_json(base, "/api/spike/snapshot")
            report_id = current(terminal)["report_id"]
            report = request_json(base, f"/api/spike/reports/{report_id}")
            before = db_digest(data / "기록/실행기록.sqlite3")
            replay_window["active"] = True
            seek(0)
            target_span = next(
                span
                for span in reversed(archive["spans"])
                if span["actor"] == "researcher-4"
                and span["control_revision"] == 2
                and span["kind"] == "model_wait"
                and span["event_ids"]
            )
            bar = page.get_by_test_id("timeline-span-" + target_span["span_id"])
            bar.scroll_into_view_if_needed()
            hit(bar)
            bar.click()
            page.get_by_test_id("event-detail").wait_for()
            event_id = page.get_by_test_id("event-detail").locator("dd").first.inner_text()
            assert event_id in target_span["event_ids"]
            hit(page.get_by_test_id("popup-close"))
            hit(page.get_by_test_id("command-input"))
            geometry("시간 막대의 원본 상세")
            close_popup()
            proof["selected_span_event"] = {"span_id": target_span["span_id"], "event_id": event_id}
            passed("시간 막대 실제 클릭·같은 기록의 원본 사건·입력/닫기 접근")
            seek(0)
            assert not page.get_by_test_id("paper-document").is_visible()
            assert page.get_by_test_id("command-submit").is_disabled()
            page.get_by_test_id("command-input").fill("재생 중에도 보존할 지시")
            capture("재생_시작.png")
            origin = datetime.fromisoformat(archive["started_at"]).timestamp() * 1000
            b_at = next(
                datetime.fromisoformat(e["occurred_at"]).timestamp() * 1000 - origin
                for e in archive["events"]
                if e["type"] == "instruction.received" and e["control_revision"] == 2
            )
            a_started = min(
                datetime.fromisoformat(e["occurred_at"]).timestamp() * 1000 - origin
                for e in archive["events"]
                if e["type"] == "model.started"
                and e["actor"].startswith("researcher-")
                and e["control_revision"] == 1
            )
            mid = round((a_started + b_at) / 2)
            seek(mid)
            page.wait_for_timeout(120)
            first = positions()
            assert not page.get_by_test_id("paper-document").is_visible()
            if page.get_by_test_id("bubble-planner").is_visible():
                assert b not in page.get_by_test_id("bubble-planner").inner_text()
            capture("재생_이전버전.png")
            slider = page.get_by_test_id("timeline-scrubber")
            end = float(slider.get_attribute("max"))
            seek(end)
            page.get_by_test_id("paper-document").wait_for(state="visible")
            seek(mid)
            page.wait_for_timeout(120)
            assert positions() == first, "같은 시점의 위치가 탐색 순서에 따라 달라짐"
            page.wait_for_timeout(300)
            assert positions() == first, "정지한 재생 장면이 실제 벽시계로 이동함"
            passed("앞/중간/뒤 seek·미래 보고서 차단·동일 시점 장면 결정성")
            player = page.get_by_test_id("replay-play")
            player.click()
            page.wait_for_function(
                '(old)=>Number(document.querySelector("[data-testid=world]").dataset.sceneTime)>old+80',
                arg=mid,
            )
            player.click()
            page.wait_for_timeout(120)
            paused = float(page.get_by_test_id("world").get_attribute("data-scene-time"))
            page.wait_for_timeout(250)
            assert float(page.get_by_test_id("world").get_attribute("data-scene-time")) == paused
            page.get_by_test_id("replay-prev").click()
            previous = float(page.get_by_test_id("world").get_attribute("data-scene-time"))
            page.get_by_test_id("replay-next").click()
            page.wait_for_function(
                '(old)=>Number(document.querySelector("[data-testid=world]").dataset.sceneTime)>=old',
                arg=previous,
            )
            passed("실제 재생/일시정지·이전/다음 단계 조작")
            slider = page.get_by_test_id("timeline-scrubber")
            slider.scroll_into_view_if_needed()
            slider.press("Home")
            page.wait_for_function(
                "Number(document.querySelector('[data-testid=world]').dataset.sceneTime)===0"
            )
            box = slider.bounding_box()
            slider.click(position={"x": box["width"] * 0.45, "y": box["height"] / 2})
            page.wait_for_function(
                "Number(document.querySelector('[data-testid=world]').dataset.sceneTime)>0"
            )
            pointer_at = float(page.get_by_test_id("world").get_attribute("data-scene-time"))
            assert 0 < pointer_at < end
            slider.press("End")
            page.wait_for_function(
                "end=>Math.abs(Number(document.querySelector('[data-testid=world]').dataset.sceneTime)-end)<2",
                arg=end,
            )
            seek(mid)
            passed("스크러버 실제 포인터·Home/End 키보드 탐색")
            capture("시간창_좁은화면.png", 390, 844)
            capture("시간창_낮은화면.png", 844, 390)
            last_track = page.get_by_test_id("timeline-track-document")
            last_track.scroll_into_view_if_needed()
            hit(last_track)
            geometry("마지막 객체 트랙 스크롤")
            drawer_close = page.get_by_test_id("timeline-drawer").get_by_role(
                "button", name="실행 타임라인 닫기", exact=True
            )
            hit(drawer_close)
            drawer_close.click()
            hit(page.get_by_test_id("command-input"))
            geometry("낮은 창 시간 드로어 접기")
            drawer_open()
            assert page.get_by_test_id("command-input").input_value() == "재생 중에도 보존할 지시"
            after = db_digest(data / "기록/실행기록.sqlite3")
            assert before == after and not proof["posts_during_replay"]
            assert (
                request_json(base, f"/api/spike/reports/{report_id}")["sha256"] == report["sha256"]
            )
            final = request_json(base, "/api/spike/snapshot")
            assert (
                final["mock_requests"] == terminal["mock_requests"]
                and final["actual_model_requests"] == 0
            )
            proof["readonly"] = {
                "before": before,
                "after": after,
                "mock_requests_before": terminal["mock_requests"],
                "mock_requests_after": final["mock_requests"],
                "actual_model_requests": 0,
                "report_sha256": report["sha256"],
            }
            passed("리플레이 동안 POST0·DB/큐/정본/호출수 불변")
            page.reload()
            page.wait_for_selector("[data-testid=world][data-render-ready=true]")
            page.evaluate('window.__timelineCanvas=document.querySelector("canvas")')
            drawer_open()
            open_replay_from_history(page, run_id)
            seek(mid)
            page.wait_for_timeout(120)
            assert not page.get_by_test_id("paper-document").is_visible()
            assert (
                not proof["posts_during_replay"]
                and db_digest(data / "기록/실행기록.sqlite3") == before
            )
            page.get_by_test_id("replay-live").scroll_into_view_if_needed()
            page.get_by_test_id("replay-live").click()
            replay_window["active"] = False
            page.wait_for_function(
                '!document.querySelector("[data-testid=command-submit]").disabled'
            ) if page.get_by_test_id("command-input").input_value() else None
            assert not proof["errors"] and not proof["external_requests"]
            passed("새로고침·기록 재열기·실시간 복귀도 무재실행")
            browser.close()
            browser = None
        proof["status"] = "PASS"
    except Exception as error:
        proof.update(status="FAIL", error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        if browser is not None:
            try:
                browser.close()
            except PlaywrightError as error:
                proof["browser_cleanup_note"] = str(error)
        if worker is not None:
            stop.touch()
            assert worker.wait(timeout=20) == 0
        log.close()
        (out / "시간_실사용검사.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {"status": proof["status"], "groups": len(proof["checks"]), "output": str(out)},
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="시간_첫검사")
    main(parser.parse_args().label)
