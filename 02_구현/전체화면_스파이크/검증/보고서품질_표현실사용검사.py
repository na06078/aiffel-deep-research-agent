"""후보 화면/실제 그래프/SQLite/렌더러를 격리 Chromium으로 검증. 외부 모델은 FakeModel뿐.
새 label별 자료를 보존하며 기존 시험/운영 자료 초기화·설치본 변경·실제 제공자 호출 없음.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pypdfium2 as pdfium
from playwright.sync_api import sync_playwright
from 화면개편_후보검사 import close_popup, hit_test, open_history_popup, page_overflow
from 화면도우미 import open_replay_from_history, return_to_live

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")
VERSION = "markdown-it4-playwright1248-svg-v2"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=20) as response:
        return json.load(response)


def eventually(fetch, predicate, timeout=25):
    end = time.monotonic() + timeout
    value = None
    while time.monotonic() < end:
        value = fetch()
        if predicate(value):
            return value
        time.sleep(0.1)
    raise AssertionError(f"상태 대기 초과: {str(value)[:1200]}")


def main(label, port):
    assert re.fullmatch(r"[A-Za-z0-9_-]+", label), "label은 단일 ASCII 식별자"
    out = SPIKE / "검증결과/보고서품질/표현실사용" / label
    work = ROOT / ".cache/보고서품질_검증/표현실사용" / label
    assert not out.exists() and not work.exists(), "기존 결과/자료 덮어쓰기 금지. 새 label 필요"
    out.mkdir(parents=True)
    work.mkdir(parents=True)
    base = f"http://127.0.0.1:{port}"
    result = {
        "label": label,
        "scope": "presentation integration only; NOT RealResearchService/Luna quality",
        "provider": "test-double",
        "external_model_requests": 0,
        "checks": [],
        "requests": [],
        "blocked": [],
        "pageerrors": [],
        "console_errors": [],
        "expected_errors": [],
        "sandbox_security_diagnostics": [],
        "captures": [],
        "started_at": datetime.now(UTC).isoformat(),
        "reports": [],
    }
    input_paths = [
        SPIKE / p
        for p in (
            "서버/앱.py",
            "서버/보고서표현.py",
            "서버/보고서변환.py",
            "서버/보고서도식.py",
            "화면/src/상세보기.tsx",
            "화면/src/보고서표현.tsx",
        )
    ]
    input_paths += sorted(p for p in (ROOT / ".cache/보고서품질_화면").rglob("*") if p.is_file())
    result["input_hashes_at_start"] = {str(p): sha(p) for p in input_paths}
    worker = None
    log = None
    browser = None
    context = None
    page = None
    baseline = json.loads((SPIKE / "검증결과/보고서품질/기준상태.json").read_text(encoding="utf-8"))

    def add(name, ok, **facts):
        result["checks"].append({"name": name, "passed": bool(ok), **facts})
        print(("PASS " if ok else "FAIL ") + name, flush=True)

    def protect():
        differences = []
        for key in ("protected_data_sha256", "installed_dist_sha256"):
            for relative, digest in baseline[key].items():
                p = ROOT / relative
                if not p.is_file() or sha(p) != digest:
                    differences.append(relative)
        add(
            "운영13파일·설치본25파일 해시 보호",
            not differences,
            differences=differences,
            protected_data_count=len(baseline["protected_data_sha256"]),
            dist_count=len(baseline["installed_dist_sha256"]),
        )
        assert not differences

    def shot(name, target=None):
        dest = out / (name + ".png")
        (target or page).screenshot(path=str(dest), full_page=False)
        result["captures"].append(str(dest))

    def check(name, fn):
        try:
            facts = fn() or {}
            add(name, True, **facts)
        except Exception as exc:  # noqa: BLE001 - 시나리오별 실패를 보존하고 최종 실패 판정에 합산한다.
            add(name, False, error=str(exc), traceback=traceback.format_exc())
            if page and not page.is_closed():
                shot("실패_" + str(len(result["checks"])))

    def menu(name):
        close_popup(page)
        if not page.get_by_test_id("drawer").is_visible():
            page.get_by_test_id("menu-toggle").click()
        page.get_by_test_id("drawer").get_by_role("button", name=name, exact=True).click()

    def open_report(report):
        close_popup(page)
        popup = open_history_popup(page)
        item = popup.locator(f'[data-testid="history-item"][data-run-id="{report["run_id"]}"]')
        item.get_by_role("button", name="자세히 보기", exact=True).click()
        page.get_by_role("button", name="최종 보고서 보기", exact=True).click()
        page.get_by_test_id("report-viewer").wait_for()

    def frame_ready():
        frame = page.frame_locator("iframe.report-presentation-frame")
        frame.locator("table").wait_for()
        return frame

    def create_fixture(suffix):
        close_popup(page)
        page.get_by_test_id("command-input").fill("표현 통합 합성 시험 " + suffix)
        page.get_by_test_id("command-submit").click()
        snap = eventually(
            lambda: get(base, "/api/spike/snapshot"),
            lambda s: len([g for g in s["waiting"] if not g["is_stale"]]) == 4,
        )
        rid = snap["current_run_id"]
        menu("제어 도구")
        page.get_by_role("button", name="현재 모두 완료", exact=True).click()
        snap = eventually(
            lambda: get(base, "/api/spike/snapshot"),
            lambda s: next(r for r in s["runs"] if r["run_id"] == rid)["status"] == "completed",
        )
        run = next(r for r in snap["runs"] if r["run_id"] == rid)
        report = get(base, "/api/spike/reports/" + run["report_id"])
        result["reports"].append(report)
        close_popup(page)
        return report

    pending = []
    mode = {"kind": "normal", "remaining": 0}

    def route_all(route):
        request = route.request
        parsed = urlsplit(request.url)
        if parsed.scheme in ("http", "https") and (
            parsed.hostname != "127.0.0.1" or parsed.port != port
        ):
            result["blocked"].append(
                {"method": request.method, "url": request.url, "reason": "non-loopback"}
            )
            route.abort()
            return
        if request.method != "GET":
            allowed = request.method == "POST" and (
                parsed.path in ("/api/spike/commands", "/api/spike/releases")
                or bool(re.fullmatch(r"/api/spike/reports/[a-f0-9]{32}/exports", parsed.path))
                or bool(re.fullmatch(r"/api/spike/runs/[a-f0-9]{32}/delete", parsed.path))
            )
            if not allowed:
                result["blocked"].append(
                    {"method": request.method, "url": request.url, "reason": "unapproved mutation"}
                )
                route.abort()
                return
        if parsed.path.endswith("/presentation") and mode["kind"] != "normal" and mode["remaining"]:
            mode["remaining"] -= 1
            response = route.fetch()
            assert response.status == 200, response.status
            if mode["kind"] == "fail":
                result["expected_errors"].append(
                    {"url": request.url, "status": 503, "injected": True}
                )
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "error": {
                                "code": "synthetic_unavailable",
                                "message": "합성 표현 조회 실패",
                            }
                        }
                    ),
                )
            else:
                pending.append((route, response))
            return
        route.continue_()

    try:
        protect()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        stop = work / "소유시험서버_종료표식"
        log = (out / "시험서버.log").open("wb")
        command = [
            sys.executable,
            str(SPIKE / "검증/보고서품질_표현시험서버.py"),
            "--data-dir",
            str(work / "데이터"),
            "--stop-file",
            str(stop),
            "--port",
            str(port),
        ]
        result["server_command"] = command
        result["command"] = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--label",
            label,
            "--port",
            str(port),
        ]
        worker = subprocess.Popen(command, cwd=str(SPIKE), stdout=log, stderr=subprocess.STDOUT)

        def health():
            assert worker.poll() is None, "소유 서버 조기 종료"
            try:
                return get(base, "/api/health")
            except OSError:
                return {}

        h = eventually(health, lambda h: h.get("status") == "ok")
        assert h["provider"] == "test-double"
        result["server_health"] = h
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                executable_path=str(
                    ROOT / ".cache/playwright/chromium-1248/chrome-win64/chrome.exe"
                ),
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                accept_downloads=True,
                service_workers="block",
            )
            context.route("**/*", route_all)
            context.on(
                "request", lambda r: result["requests"].append({"method": r.method, "url": r.url})
            )
            page = context.new_page()
            page.set_default_timeout(12000)
            page.on("pageerror", lambda e: result["pageerrors"].append(str(e)))

            def console_error(message):
                if message.type == "error":
                    record = {"text": message.text, "location": message.location}
                    if "503" in message.text and result["expected_errors"]:
                        result["expected_errors"].append({"console": record})
                    elif (
                        message.text
                        == "Blocked script execution in 'about:srcdoc' because the document's frame is sandboxed and the 'allow-scripts' permission is not set."
                        or ("Blocked opening" in message.text and "sandboxed" in message.text)
                    ):
                        # Chromium 정책 진단 원문을 별도 보존. script DOM 0/빈 sandbox와
                        # 아래 본문 링크 클릭 음성 검사로 실행/탐색 차단을 독립 검증합니다.
                        result["sandbox_security_diagnostics"].append(record)
                    else:
                        result["console_errors"].append(record)

            page.on("console", console_error)
            page.goto(base + "/#/control")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.evaluate("window.__ownedCanvas=document.querySelector('canvas')")
            reports = [create_fixture(suffix) for suffix in ("A", "B", "C")]
            a, b, c = reports
            metrics_before = get(base, "/__test__/metrics")
            add(
                "UI지시·4gate UI해제·신규보고서3개·외부모델0",
                all("합성" in r["markdown"] for r in reports)
                and metrics_before["calls"]
                == {"plan": 3, "research": 12, "synthesize": 3, "external_model": 0, "web": 0},
                metrics=metrics_before,
            )

            def graph_sqlite():
                db = work / "데이터/실행기록.sqlite3"
                with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as con:
                    tables = [
                        r[0]
                        for r in con.execute("select name from sqlite_master where type='table'")
                    ]
                    counts = {
                        t: con.execute('select count(*) from "' + t + '"').fetchone()[0]
                        for t in tables
                    }
                archive = get(base, "/api/spike/runs/" + a["run_id"] + "/archive")
                (out / "실제그래프_아카이브.json").write_text(
                    json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                labels = sorted({s["label"] for s in archive["spans"] if s["kind"] == "node"})
                assert {"plan", "research", "review", "synthesize", "evaluate", "publish"}.issubset(
                    labels
                ), labels
                assert counts["reports"] == 3
                return {"db": str(db), "counts": counts, "node_labels": labels}

            check("실제6노드·SQLite 저장", graph_sqlite)

            open_report(a)

            def content():
                frame = frame_ready()
                assert frame.locator("table tbody tr").count() == 2
                assert "한글 보존" in frame.locator("table").inner_text()
                assert frame.locator("svg").count() == 1
                assert "합성 검증 도식" in frame.locator("figcaption").inner_text()
                assert frame.locator("script,img").count() == 0
                assert (
                    "<script>window.__presentationUnsafe=true</script>"
                    in frame.locator("body").inner_text()
                )
                assert page.locator("iframe").get_attribute("sandbox") == ""
                page.locator(".report-markdown-source summary").click()
                assert page.locator("pre.markdown").inner_text() == a["markdown"]
                page.locator(".report-markdown-source summary").click()
                page.locator(".report-source-links summary").click()
                link = page.locator(".report-source-links a")
                assert link.get_attribute("href") == "https://example.invalid/presentation-source"
                assert link.get_attribute("target") == "_blank"
                assert set(link.get_attribute("rel").split()) >= {"noopener", "noreferrer"}
                link.focus()
                assert link.evaluate("e=>e===document.activeElement")
                page.keyboard.press("Tab")
                return {
                    "iframe_body_text": frame.locator("body").inner_text(),
                    "source_link": link.get_attribute("href"),
                }

            check("iframe 실제 표·SVG·escaped Markdown·접힌원문·외부출처 안전링크", content)

            def source_navigation():
                frame = frame_ready()
                body_link = frame.get_by_role("link", name="검증용 출처 한글", exact=True)
                assert body_link.get_attribute("target") == "_blank"
                assert set(body_link.get_attribute("rel").split()) >= {"noopener", "noreferrer"}
                requests_before = len(result["requests"])
                pages_before = len(context.pages)
                body_link.click()
                page.wait_for_timeout(350)
                new_requests = result["requests"][requests_before:]
                assert not [
                    r for r in new_requests if urlsplit(r["url"]).hostname == "example.invalid"
                ], new_requests
                assert len(context.pages) == pages_before
                assert frame.locator("table").count() == frame.locator("svg").count() == 1
                assert page.locator("iframe").get_attribute("sandbox") == ""
                # 승인된 바깥 출처 링크만 새 창. 실제 외부 HTTP 대신 명시적인 메모리 응답.
                url = "https://example.invalid/presentation-source"
                intercepted = []

                def memory_source(route):
                    intercepted.append(route.request.url)
                    route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body='<html lang="ko"><meta charset="utf-8"><body>외부 출처 탐색 대역 · 실제 외부 HTTP 없음</body></html>',
                    )

                context.route(url, memory_source)
                try:
                    with context.expect_page() as opening:
                        page.locator(".report-source-links a").click()
                    popup = opening.value
                    popup.wait_for_load_state()
                    popup.get_by_text(
                        "외부 출처 탐색 대역 · 실제 외부 HTTP 없음", exact=True
                    ).wait_for()
                    assert "탐색 대역" in popup.locator("body").inner_text()
                    assert intercepted == [url]
                    shot("바깥출처링크_메모리응답", popup)
                    popup.close()
                finally:
                    context.unroute(url, memory_source)
                result["memory_route_navigations"] = intercepted
                return {
                    "inside_attempts": 0,
                    "outside_memory_navigations": intercepted,
                    "actual_external_http": 0,
                }

            check(
                "본문출처 클릭은 sandbox 안 탐색/요청0·바깥목록 링크만 새창(메모리대역)",
                source_navigation,
            )

            saved = {}

            def exports():
                md = page.get_by_role("link", name="Markdown 다운로드", exact=True)
                with page.expect_download() as waiting:
                    md.click()
                p = work / "합성보고서.md"
                waiting.value.save_as(str(p))
                saved["md"] = p
                assert sha(p) == a["sha256"]
                for fmt in ("html", "pdf"):
                    page.get_by_test_id(f"export-{fmt}-create").click()
                    link = page.get_by_test_id(f"export-{fmt}-download")
                    link.wait_for(timeout=45000)
                    with page.expect_download() as waiting:
                        link.click()
                    dest = work / ("합성보고서." + fmt)
                    waiting.value.save_as(str(dest))
                    saved[fmt] = dest
                    assert dest.stat().st_size > 1000
                    assert a["sha256"] in link.get_attribute("href")
                html = context.new_page()
                html.goto(saved["html"].as_uri())
                assert html.locator("table").count() == html.locator("svg").count() == 1
                assert "가|나" in html.locator("table").inner_text()
                assert "합성 검증 도식" in html.locator("figcaption").inner_text()
                assert (
                    html.locator("a").get_attribute("href")
                    == "https://example.invalid/presentation-source"
                )
                assert html.evaluate("window.__presentationUnsafe===undefined")
                html.locator("table").scroll_into_view_if_needed()
                shot("HTML_재열기_표도식", html)
                assert (
                    html.locator("body").inner_text() == frame_ready().locator("body").inner_text()
                )
                html.close()
                doc = pdfium.PdfDocument(saved["pdf"])
                text = ""
                for i in range(len(doc)):
                    pp = doc[i]
                    tp = pp.get_textpage()
                    text += tp.get_text_range()
                    tp.close()
                    image = pp.render(scale=1.25).to_pil()
                    dest = out / f"PDF_페이지_{i + 1}.png"
                    image.save(dest)
                    result["captures"].append(str(dest))
                    pp.close()
                pages = len(doc)
                doc.close()
                normalized = re.sub(r"\s+", "", text)
                for phrase in (
                    "한글보존",
                    "검증값42",
                    "합성검증도식",
                    "입력에서보고서까지",
                    "입력→합성표→보고서",
                    "검증용출처한글",
                ):
                    assert phrase in normalized, phrase
                (out / "PDF_추출본문.txt").write_text(text, encoding="utf-8")
                return {
                    "pages": pages,
                    "downloads": {
                        k: {"path": str(p), "bytes": p.stat().st_size, "sha256": sha(p)}
                        for k, p in saved.items()
                    },
                    "source_md_sha256": a["sha256"],
                }

            check("MD정본 해시·HTML/PDF 실제 생성/다운로드/재열기·한글표도식 대조", exports)

            draft = "미전송 입력 보존 · 표현 통합 시험"
            page.get_by_test_id("command-input").fill(draft)
            for width, height in ((1440, 900), (390, 844), (844, 390)):

                def layout(width=width, height=height):
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_function(
                        "()=>{const w=document.querySelector('[data-testid=world]');return Math.abs(Number(w.dataset.layoutWidth)-innerWidth)<4&&Math.abs(Number(w.dataset.layoutHeight)-innerHeight)<4}"
                    )
                    frame = frame_ready()
                    iframe = page.locator("iframe.report-presentation-frame")
                    iframe.scroll_into_view_if_needed()
                    # 부모 팝업보다 큰 iframe에서 실제 휠로 도식 글자까지 이동합니다.
                    wheel_steps = []
                    for _ in range(35):
                        box = iframe.bounding_box()
                        popup_box = (
                            page.get_by_test_id("popup").locator(".popup-body").bounding_box()
                        )
                        top = max(box["y"], popup_box["y"])
                        bottom = min(box["y"] + box["height"], popup_box["y"] + popup_box["height"])
                        label_box = frame.locator("svg text").bounding_box()
                        # Playwright bounding_box는 하위 frame도 최상위 viewport 좌표입니다.
                        wheel_steps.append({"iframe": box, "popup": popup_box, "label": label_box})
                        if label_box["y"] >= top and label_box["y"] + label_box["height"] <= bottom:
                            break
                        page.mouse.move(box["x"] + 15, (top + bottom) / 2)
                        distance = label_box["y"] + label_box["height"] / 2 - (top + bottom) / 2
                        direction = max(-160, min(160, distance))
                        page.mouse.wheel(0, direction)
                        page.wait_for_timeout(70)
                    assert (
                        label_box["y"] >= top and label_box["y"] + label_box["height"] <= bottom
                    ), wheel_steps
                    frame.locator("svg").wait_for()
                    measures = {
                        "overflow": page_overflow(page),
                        "canvas": page.evaluate(
                            "()=>({count:document.querySelectorAll('canvas').length,same:window.__ownedCanvas===document.querySelector('canvas')})"
                        ),
                    }
                    measures["svg_labels"] = frame.locator("svg text").evaluate_all(
                        "es=>es.map(e=>{const r=e.getBoundingClientRect(),s=e.ownerSVGElement.getBoundingClientRect();return {text:e.textContent,width:r.width,height:r.height,inside:r.left>=s.left&&r.right<=s.right&&r.top>=s.top&&r.bottom<=s.bottom}})"
                    )
                    assert all(x["inside"] and x["width"] > 0 for x in measures["svg_labels"])
                    shot(f"뷰어_{width}x{height}")
                    for tid in ("command-input", "popup-close"):
                        m = hit_test(page.get_by_test_id(tid))
                        measures[tid] = m
                        assert m["inView"] and m["hit"], (tid, m)
                    last = page.locator(".report-markdown-source summary")
                    last.scroll_into_view_if_needed()
                    measures["last_control"] = hit_test(last)
                    assert measures["last_control"]["inView"] and measures["last_control"]["hit"]
                    last.focus()
                    assert last.evaluate("e=>e===document.activeElement")
                    page.keyboard.press("Enter")
                    assert page.locator(".report-markdown-source").get_attribute("open") is not None
                    page.keyboard.press("Enter")
                    iframe.focus()
                    assert iframe.evaluate("e=>e===document.activeElement")
                    assert measures["overflow"]["ok"] and measures["canvas"] == {
                        "count": 1,
                        "same": True,
                    }
                    assert page.get_by_test_id("command-input").input_value() == draft
                    return measures

                check(f"실제뷰포트 {width}x{height}·표도식label·입력닫기끝조작·키보드focus", layout)

            page.set_viewport_size({"width": 1440, "height": 900})

            def retry():
                mode.update(kind="fail", remaining=1)
                open_report(a)
                page.get_by_role("button", name="본문 다시 조회", exact=True).wait_for()
                assert page.locator("iframe").count() == 0
                assert page.get_by_role("link", name="Markdown 다운로드", exact=True).is_visible()
                shot("조회실패_명시재시도")
                mode.update(kind="normal", remaining=0)
                page.get_by_role("button", name="본문 다시 조회", exact=True).click()
                frame_ready()

            check("자동GET 503 실패·원문다운로드 유지·명시적 재조회 성공", retry)

            # 실제 GET의 200 응답을 보류한 뒤 UI 수명 변경. 성공/실패 모두 늦게 전달한다.
            for action in ("close", "replay-before-submitted", "other-report"):
                for fail in (False, True):

                    def lifecycle(action=action, fail=fail):
                        close_popup(page)
                        if (
                            page.get_by_test_id("timeline-subject").count()
                            and page.get_by_test_id("timeline-subject").get_attribute("data-mode")
                            == "replay"
                        ):
                            return_to_live(page)
                        mode.update(kind="hold", remaining=1)
                        open_report(a)
                        page.wait_for_function(
                            "()=>document.querySelector('[data-testid=report-presentation]')?.textContent.includes('불러오는 중')"
                        )
                        eventually(
                            lambda: (page.wait_for_timeout(30), len(pending))[1], lambda n: n == 1
                        )
                        if action == "close":
                            close_popup(page)
                        elif action == "replay-before-submitted":
                            close_popup(page)
                            open_replay_from_history(page, a["run_id"])
                            slider = page.get_by_test_id("timeline-scrubber")
                            slider.focus()
                            page.keyboard.press("Home")
                            page.wait_for_timeout(100)
                        else:
                            open_report(c)
                            frame_ready()
                        route, response = pending.pop()
                        if fail:
                            result["expected_errors"].append(
                                {
                                    "url": route.request.url,
                                    "status": 503,
                                    "injected": True,
                                    "lifecycle": action,
                                }
                            )
                            route.fulfill(
                                status=503,
                                content_type="application/json",
                                body=json.dumps(
                                    {
                                        "error": {
                                            "code": "synthetic_delayed",
                                            "message": "지연 합성 실패",
                                        }
                                    }
                                ),
                            )
                        else:
                            route.fulfill(response=response)
                        mode.update(kind="normal", remaining=0)
                        page.wait_for_timeout(500)
                        if action == "other-report":
                            assert (
                                c["report_id"] in page.get_by_test_id("report-viewer").inner_text()
                            )
                            assert (
                                a["report_id"]
                                not in page.get_by_test_id("report-viewer").inner_text()
                            )
                        else:
                            assert not page.get_by_test_id("report-viewer").is_visible()
                        assert (
                            page.get_by_role("button", name="본문 다시 조회", exact=True).count()
                            == 0
                        )
                        shot(f"지연_{action}_{'실패' if fail else '성공'}")
                        if action == "replay-before-submitted":
                            assert page.get_by_test_id("command-submit").is_disabled()
                            return_to_live(page)
                        close_popup(page)

                    check(
                        f"지연GET {action} 후 {'실패' if fail else '성공'} 응답은 옛뷰어 복원금지",
                        lifecycle,
                    )

            def replay_refresh():
                close_popup(page)
                before = get(base, "/__test__/metrics")["calls"]
                open_replay_from_history(page, c["run_id"])
                page.reload()
                page.get_by_test_id("timeline-toggle").wait_for()
                if not page.get_by_test_id("timeline-subject").is_visible():
                    page.get_by_test_id("timeline-toggle").click()
                page.wait_for_function(
                    "id=>document.querySelector('[data-testid=timeline-subject]')?.dataset.runId===id&&document.querySelector('[data-testid=timeline-subject]')?.dataset.mode==='replay'",
                    arg=c["run_id"],
                )
                slider = page.get_by_test_id("timeline-scrubber")
                slider.focus()
                page.keyboard.press("Home")
                assert not page.get_by_test_id("report-viewer").is_visible()
                return_to_live(page)
                page.reload()
                page.get_by_test_id("command-input").wait_for()
                after = get(base, "/__test__/metrics")["calls"]
                assert before == after
                return {"before": before, "after": after}

            check("재생/되감기/재생중새로고침/실시간새로고침 모델·웹 추가0", replay_refresh)

            # 연속 지시는 이전 보고서를 문맥으로 쓴다. 삭제 펜스를 유지하여 C→B→A 순서.
            for report, fail in ((c, False), (b, True), (a, False)):

                def delete_lifecycle(report=report, fail=fail):
                    mode.update(kind="hold", remaining=1)
                    open_report(report)
                    eventually(
                        lambda: (page.wait_for_timeout(30), len(pending))[1], lambda n: n == 1
                    )
                    menu("과거 기록")
                    item = page.locator(
                        f'[data-testid="history-item"][data-run-id="{report["run_id"]}"]'
                    )
                    item.get_by_test_id("history-delete").click()
                    item.get_by_test_id("history-delete-yes").click()
                    item.wait_for(state="detached")
                    route, response = pending.pop()
                    if fail:
                        result["expected_errors"].append(
                            {
                                "url": route.request.url,
                                "status": 503,
                                "injected": True,
                                "lifecycle": "delete",
                            }
                        )
                        route.fulfill(
                            status=503,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "error": {
                                        "code": "synthetic_delayed",
                                        "message": "삭제 후 지연실패",
                                    }
                                }
                            ),
                        )
                    else:
                        route.fulfill(response=response)
                    mode.update(kind="normal", remaining=0)
                    page.wait_for_timeout(500)
                    assert not page.get_by_test_id("report-viewer").is_visible()
                    for path in (
                        "/api/spike/reports/" + report["report_id"],
                        "/api/spike/reports/"
                        + report["report_id"]
                        + "/presentation?source_report_hash="
                        + report["sha256"]
                        + "&renderer_version="
                        + VERSION,
                    ):
                        try:
                            get(base, path)
                            raise AssertionError("삭제 보고서/표현 GET이 성공함")
                        except urllib.error.HTTPError as exc:
                            assert exc.code == 404
                    exports = list((work / "데이터/형식변환").glob("*"))
                    if report is a:
                        assert not exports, [str(p) for p in exports]
                    snapshot = get(base, "/api/spike/snapshot")
                    assert not any(r["run_id"] == report["run_id"] for r in snapshot["runs"])
                    shot(f"삭제후_{report['report_id']}_지연{'실패' if fail else '성공'}")
                    return {
                        "deleted_run": report["run_id"],
                        "remaining_derivatives": [str(p) for p in exports],
                    }

                check(
                    f"합성fixture 삭제·파생파일제거·지연{'실패' if fail else '성공'} 부활금지",
                    delete_lifecycle,
                )

            final_metrics = get(base, "/__test__/metrics")
            result["pdf_browser_requests"] = final_metrics["pdf_browser_requests"]
            result["real_renderer_invocations"] = final_metrics["real_renderer_invocations"]
            add(
                "실제 HTML/PDF 렌더러 각1회·PDF Chromium 네트워크요청0",
                not result["pdf_browser_requests"]
                and [r["format"] for r in result["real_renderer_invocations"]] == ["html", "pdf"],
                requests=result["pdf_browser_requests"],
                invocations=result["real_renderer_invocations"],
            )
            add(
                "조회·다운로드·변환·수명·삭제는 모델/웹 추가호출0",
                final_metrics["calls"] == metrics_before["calls"],
                before=metrics_before,
                after=final_metrics,
            )
            add(
                "외부네트워크·미승인변경·예상밖console/pageerror0",
                not result["blocked"] and not result["console_errors"] and not result["pageerrors"],
                blocked=result["blocked"],
                console_errors=result["console_errors"],
                pageerrors=result["pageerrors"],
            )
            context.unroute_all(behavior="ignoreErrors")
            context.close()
            context = None
            browser.close()
            browser = None
    except Exception as exc:  # noqa: BLE001 - 격리 검증기의 최종 실패를 기록하며 성공으로 바꾸지 않는다.
        add("검사 공통경로", False, error=str(exc), traceback=traceback.format_exc())
        if page and not page.is_closed():
            try:
                shot("공통실패")
            except Exception as capture_error:  # noqa: BLE001 - 원래 실패를 캡처 오류로 덮지 않는다.
                result.setdefault("capture_errors", []).append(
                    {"type": type(capture_error).__name__, "message": str(capture_error)}
                )
    finally:
        if worker is not None:
            # Popen으로 생성한 소유 서버만 marker로 정상 종료. PID 이름 기반 kill 없음.
            stop.touch()
            try:
                code = worker.wait(timeout=20)
                add("소유시험서버 정상종료", code == 0, pid=worker.pid, returncode=code)
            except subprocess.TimeoutExpired:
                add(
                    "소유시험서버 정상종료",
                    False,
                    pid=worker.pid,
                    error="marker 이후 20초 초과; 임의 kill 안 함",
                )
        if log:
            log.close()
        try:
            protect()
        except (AssertionError, OSError) as protection_error:
            add("최종 보호검사 예외", False, error=str(protection_error))
        result["input_hashes_at_end"] = {str(p): sha(p) for p in input_paths}
        add(
            "검사중 표현API/렌더러/화면소스·후보bundle drift0",
            result["input_hashes_at_start"] == result["input_hashes_at_end"],
        )
        result["source_hashes"] = {
            str(SPIKE / "검증" / name): sha(SPIKE / "검증" / name)
            for name in ("보고서품질_표현시험서버.py", "보고서품질_표현실사용검사.py")
        }
        result["summary"] = {
            "total": len(result["checks"]),
            "passed": sum(c["passed"] for c in result["checks"]),
            "failed": [c["name"] for c in result["checks"] if not c["passed"]],
            "browser_requests": len(result["requests"]),
            "post_requests": sum(r["method"] == "POST" for r in result["requests"]),
            "external_model_requests": 0,
            "actual_external_http": 0,
            "outside_memory_route_navigations": len(result.get("memory_route_navigations", [])),
            "sandbox_security_diagnostics": len(result["sandbox_security_diagnostics"]),
            "expected_injected_http_503": sum(
                e.get("status") == 503 for e in result["expected_errors"]
            ),
        }
        (out / "확인.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {"evidence": str(out / "확인.json"), **result["summary"]}, ensure_ascii=False
            ),
            flush=True,
        )
    return 1 if result["summary"]["failed"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--port", type=int, default=8794)
    args = parser.parse_args()
    raise SystemExit(main(args.label, args.port))
