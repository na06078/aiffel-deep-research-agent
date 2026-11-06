"""HTML/PDF의 실제 팝업·HTTP·렌더·다운로드·재열기를 검증한다. 연구 재실행 금지."""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def http(base, path, body=None):
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={} if body is None else {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def eventually(fetch, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fetch()
        if predicate(value):
            return value
        time.sleep(0.1)
    raise AssertionError(f"기대 상태 대기 시간 초과: {value}")


def source_text_lines(markdown):
    """이번 실제/합성 입력의 표준 Markdown을 별도 규칙으로 대조한다. 렌더러 재사용 금지."""
    expected = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line or line.startswith(("|", "![", "<script>")):
            continue
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"^#{1,6}\s+|^[-*]\s+", "", line)
        line = line.replace("**", "").replace("_", "").replace("`", "")
        expected.append(re.sub(r"\s+", "", line))
    return expected


def fixture_report(base, instruction):
    admission = http(
        base,
        "/api/spike/commands",
        {"text": instruction, "client_request_id": "export-" + instruction},
    )
    state = eventually(
        lambda: http(base, "/api/spike/snapshot"),
        lambda value: len([x for x in value["waiting"] if not x["is_stale"]]) == 4,
    )
    http(
        base,
        "/api/spike/releases",
        {
            "gate_ids": [x["gate_id"] for x in state["waiting"] if not x["is_stale"]],
            "outcome": "success",
        },
    )
    state = eventually(
        lambda: http(base, "/api/spike/snapshot"),
        lambda value: (
            next(x for x in value["runs"] if x["run_id"] == admission["run_id"])["status"]
            == "completed"
        ),
    )
    run = next(x for x in state["runs"] if x["run_id"] == admission["run_id"])
    return http(base, f"/api/spike/reports/{run['report_id']}")


def run(base, label, isolated, port):
    evidence = SPIKE / "검증결과" / label
    files = ROOT / ".cache/보고서변환_실사용" / label
    evidence.mkdir(parents=True, exist_ok=True)
    files.mkdir(parents=True, exist_ok=True)
    proof = {
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "isolated" if isolated else "actual",
        "checks": [],
        "posts": [],
        "browser_errors": [],
        "external_requests": [],
        "captures": [],
    }
    worker = None
    worker_log = None
    stop_file = files / "종료표식"

    def passed(name, **facts):
        proof["checks"].append({"name": name, "status": "PASS", **facts})
        print("PASS " + name, flush=True)

    def start_worker(fail):
        nonlocal worker, worker_log
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
        stop_file.unlink(missing_ok=True)
        args = [
            sys.executable,
            str(SPIKE / "검증/보고서변환_시험서버.py"),
            "--data-dir",
            str(files / "데이터"),
            "--stop-file",
            str(stop_file),
            "--port",
            str(port),
        ]
        if fail:
            args.append("--fail-first-pdf")
        worker_log = (files / "시험서버.log").open("ab")
        worker = subprocess.Popen(args, cwd=SPIKE, stdout=worker_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise AssertionError(f"시험 서버 종료: {worker.returncode}")
            try:
                if http(base, "/api/health")["status"] == "ok":
                    return
            except OSError:
                pass
            time.sleep(0.2)
        raise AssertionError("시험 서버 기동 시간 초과")

    def stop_worker():
        nonlocal worker, worker_log
        if worker is not None:
            stop_file.touch()
            code = worker.wait(timeout=15)
            assert code == 0, code
            worker = None
        if worker_log is not None:
            worker_log.close()
            worker_log = None

    try:
        if isolated:
            start_worker(True)
            assert http(base, "/api/health")["provider"] == "test-double"
            report_a = fixture_report(base, "합성 변환 A")
            report_b = fixture_report(base, "합성 긴 본문 B")
            reports = [report_a, report_b]
        else:
            baseline = json.loads(
                (SPIKE / "검증결과/보고서변환_기준상태.json").read_text(encoding="utf-8")
            )
            for relative, item in baseline["protected_files"].items():
                assert digest(ROOT / relative) == item["sha256"], relative
            initial = http(base, "/api/spike/snapshot")
            assert initial["actual_model_requests"] == 27
            assert all(x["status"] != "running" for x in initial["runs"]) and not initial["queue"]
            reports = [http(base, f"/api/spike/reports/{baseline['runs'][0]['report_id']}")]
        state_before = http(base, "/api/spike/snapshot")
        proof["model_attempts_before"] = state_before["actual_model_requests"]
        proof["reports"] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, accept_downloads=True
            )
            page = context.new_page()
            page.on("pageerror", lambda error: proof["browser_errors"].append(str(error)))

            def record_request(request):
                if request.method == "POST":
                    proof["posts"].append(request.url)
                if request.url.startswith(("http:", "https:")) and not request.url.startswith(
                    base + "/"
                ):
                    proof["external_requests"].append(request.url)

            page.on("request", record_request)
            page.goto(base + "/#/control")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            page.evaluate("window.__exportCanvas=document.querySelector('canvas')")
            draft = "HTML/PDF 검증 중 보존할 미전송 지시"
            page.get_by_test_id("command-input").fill(draft)

            def close_popup():
                close = page.get_by_test_id("popup-close")
                if close.count() and close.is_visible():
                    close.click()

            def open_report(report):
                close_popup()
                if not page.get_by_test_id("drawer").is_visible():
                    page.get_by_test_id("menu-toggle").click()
                page.get_by_test_id("drawer").get_by_role(
                    "button", name="과거 기록", exact=True
                ).click()
                run = next(x for x in state_before["runs"] if x["report_id"] == report["report_id"])
                item = (
                    page.get_by_test_id("popup")
                    .locator(".record-list>li")
                    .filter(has_text=run["question"])
                )
                item.get_by_role("button", name="자세히 보기", exact=True).click()
                page.get_by_role("button", name="최종 보고서 보기", exact=True).click()
                page.get_by_test_id("report-exports").wait_for()
                assert (
                    page.get_by_test_id("report-viewer").locator("pre.markdown").inner_text()
                    == report["markdown"]
                )

            def ensure_export(format):
                link = page.get_by_test_id(f"export-{format}-download")
                if not link.is_visible():
                    page.get_by_test_id(f"export-{format}-create").click()
                    link.wait_for(timeout=30000)
                return link

            def capture(name, width, height):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_function(
                    """() => {const w=document.querySelector('[data-testid=world]'); return Math.abs(Number(w?.dataset.layoutWidth)-innerWidth)<4&&Math.abs(Number(w?.dataset.layoutHeight)-innerHeight)<4;}"""
                )
                page.evaluate("document.fonts.ready")
                for format in ("html", "pdf"):
                    target = page.get_by_test_id(f"export-{format}-download")
                    if target.is_visible():
                        target.scroll_into_view_if_needed()
                        assert target.evaluate(
                            "e=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}"
                        )
                geometry = page.evaluate(
                    """() => {const w=document.querySelector('[data-testid=world]').getBoundingClientRect();return {width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollX,scrollY,canvasCount:document.querySelectorAll('canvas').length,sameCanvas:window.__exportCanvas===document.querySelector('canvas'),world:{x:w.x,y:w.y,width:w.width,height:w.height}};}"""
                )
                assert (
                    geometry["scrollWidth"] <= width
                    and geometry["scrollX"] == geometry["scrollY"] == 0
                )
                assert geometry["canvasCount"] == 1 and geometry["sameCanvas"]
                assert geometry["world"] == {"x": 0, "y": 0, "width": width, "height": height}
                assert page.get_by_test_id("command-input").input_value() == draft
                page.screenshot(path=str(evidence / name))
                proof["captures"].append({"file": name, **geometry})

            def download_and_reopen(report, suffix):
                saved = {}
                for format in ("html", "pdf"):
                    link = ensure_export(format)
                    href = link.get_attribute("href")
                    assert f"/reports/{report['report_id']}/exports/" in href, href
                    assert report["sha256"] in href, href
                    with page.expect_download() as pending:
                        link.click()
                    assert (
                        pending.value.suggested_filename
                        == f"연구보고서_{report['report_id']}.{format}"
                    )
                    path = files / f"{suffix}보고서.{format}"
                    pending.value.save_as(str(path))
                    saved[format] = path
                expected = source_text_lines(report["markdown"])
                exported = context.new_page()
                outbound = []
                exported.on(
                    "request",
                    lambda request: (
                        outbound.append(request.url)
                        if request.url.startswith(("http:", "https:"))
                        else None
                    ),
                )
                exported.goto(saved["html"].as_uri())
                html_text = re.sub(r"\s+", "", exported.locator("body").inner_text())
                assert not [text for text in expected if text not in html_text], "HTML 본문 누락"
                assert exported.evaluate("window.__unsafeExport===undefined")
                assert not outbound, outbound
                html_capture = suffix + "_HTML_재열기.png"
                exported.screenshot(path=str(evidence / html_capture), full_page=False)
                proof["captures"].append({"file": html_capture})
                exported.close()
                document = pdfium.PdfDocument(saved["pdf"])
                pdf_text = []
                images = []
                for index in range(len(document)):
                    pdf_page = document[index]
                    text_page = pdf_page.get_textpage()
                    pdf_text.append(text_page.get_text_range())
                    text_page.close()
                    bitmap = pdf_page.render(scale=1.2)
                    image = bitmap.to_pil().copy()
                    images.append(image)
                    image.save(evidence / f"{suffix}_PDF_{index + 1:02}.png")
                    bitmap.close()
                    pdf_page.close()
                page_count = len(document)
                document.close()
                compact_pdf = re.sub(r"\s+", "", "".join(pdf_text))
                missing = [text[:80] for text in expected if text not in compact_pdf]
                assert not missing, ("PDF 본문 누락", missing)
                if isolated and report["report_id"] == reports[-1]["report_id"]:
                    assert page_count >= 3, page_count
                width = max(image.width for image in images)
                height = max(image.height for image in images) + 30
                sheet = Image.new("RGB", (width * 2, height * ((len(images) + 1) // 2)), "#e7e7e7")
                draw = ImageDraw.Draw(sheet)
                for index, image in enumerate(images):
                    x, y = index % 2 * width, index // 2 * height
                    draw.text((x + 12, y + 5), f"Page {index + 1}", fill="black")
                    sheet.paste(image, (x, y + 30))
                contact = suffix + "_PDF_전체쪽.png"
                sheet.save(evidence / contact)
                proof["captures"].append({"file": contact})
                meta = {
                    "report_id": report["report_id"],
                    "source_sha256": report["sha256"],
                    "html_file": str(saved["html"].relative_to(ROOT)),
                    "pdf_file": str(saved["pdf"].relative_to(ROOT)),
                    "html_sha256": digest(saved["html"]),
                    "pdf_sha256": digest(saved["pdf"]),
                    "pdf_pages": page_count,
                    "source_text_lines_checked": len(expected),
                }
                proof["reports"].append(meta)
                return meta

            open_report(reports[0])
            ensure_export("html")
            if isolated:
                page.get_by_test_id("export-pdf-create").click()
                eventually(
                    lambda: http(base, f"/api/spike/reports/{reports[0]['report_id']}/exports"),
                    lambda value: value["exports"][1]["status"] == "failed",
                )
                page.get_by_test_id("export-pdf-create").get_by_text("PDF", exact=False).count()
                page.wait_for_function(
                    "document.querySelector('[data-testid=report-exports]').textContent.includes('다시 시도')"
                )
                failed_state = http(base, f"/api/spike/reports/{reports[0]['report_id']}/exports")
                assert failed_state["exports"][1]["error"]["code"] == "export_failed"
                passed("격리 실제 HTTP 변환 실패 표시와 Markdown 보존")
            ensure_export("pdf")
            first_meta = download_and_reopen(reports[0], "첫")
            passed("같은 정본의 HTML/PDF 생성·다운로드·실제 재열기", **first_meta)
            capture("보고서_넓은창.png", 1440, 900)
            capture("보고서_좁은창.png", 390, 844)
            capture("보고서_낮은창.png", 844, 390)
            passed("세 해상도에서 변환 조작·닫기·입력·같은 마을 보존")
            page.set_viewport_size({"width": 1440, "height": 900})
            if isolated:
                open_report(reports[1])
                page.get_by_test_id("export-html-create").click()
                open_report(reports[0])
                page.wait_for_timeout(1400)
                for format in ("html", "pdf"):
                    assert reports[0]["report_id"] in page.get_by_test_id(
                        f"export-{format}-download"
                    ).get_attribute("href")
                open_report(reports[1])
                ensure_export("html")
                assert not page.get_by_test_id("export-pdf-download").is_visible()
                ensure_export("pdf")
                second_meta = download_and_reopen(reports[1], "긴")
                passed("보고서 전환·늦은 응답 격리 및 긴 문서의 실제 페이지 변환", **second_meta)
                persisted = http(base, f"/api/spike/reports/{reports[1]['report_id']}/exports")
                # SSE를 연 페이지부터 해제한다. 브라우저 연결을 기다리는 graceful 종료를
                # 변환 자원 정리 실패로 오인하지 않으며, 재접속/파일 재사용은 아래에서 검사한다.
                page.goto("about:blank")
                stop_worker()
                start_worker(False)
                page.goto(base + "/#/control")
                page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
                page.evaluate("window.__exportCanvas=document.querySelector('canvas')")
                open_report(reports[1])
                assert (
                    http(base, f"/api/spike/reports/{reports[1]['report_id']}/exports") == persisted
                )
                for format in ("html", "pdf"):
                    link = page.get_by_test_id(f"export-{format}-download")
                    link.wait_for()
                    with page.expect_download() as pending:
                        link.click()
                    restored = files / f"재시작보고서.{format}"
                    pending.value.save_as(str(restored))
                    assert digest(restored) == second_meta[f"{format}_sha256"]
                passed("실제 서버 재시작 후 파생 파일/상태 재사용·무재조사")
            else:
                page.reload()
                page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
                open_report(reports[0])
                for format in ("html", "pdf"):
                    page.get_by_test_id(f"export-{format}-download").wait_for()
                passed("실제 사용자 실행본의 새로고침·과거 보고서 변환 복원")
            after = http(base, "/api/spike/snapshot")
            assert after == state_before, "변환이 연구/큐/호출 상태를 변경했습니다."
            proof["model_attempts_after"] = after["actual_model_requests"]
            assert all("/exports" in url for url in proof["posts"]), proof["posts"]
            assert not proof["browser_errors"] and not proof["external_requests"], proof
            if not isolated:
                for relative, item in baseline["protected_files"].items():
                    assert digest(ROOT / relative) == item["sha256"], relative
                proof["protected_files_unchanged"] = True
            passed("변환 외 POST 없음·모델/상태/큐/정본 불변·외부 자원 요청 없음")
            context.close()
            browser.close()
        proof["status"] = "PASS"
    except Exception as error:  # noqa: BLE001 -- 실제 실패 원인을 근거로 보존한다.
        proof["status"] = "FAIL"
        proof["error"] = str(error)
        proof["traceback"] = traceback.format_exc()
        print(proof["traceback"], flush=True)
    finally:
        if isolated:
            try:
                stop_worker()
            except Exception as error:  # noqa: BLE001 -- 소유 시험 작업자의 정리 실패도 실패다.
                proof["status"] = "FAIL"
                proof["cleanup_error"] = str(error)
        proof["finished_at"] = datetime.now(UTC).isoformat()
        (evidence / "보고서변환_실사용검사.json").write_text(
            json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("EXPORT_WORKFLOW=" + proof["status"], flush=True)
    return 0 if proof["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8768")
    parser.add_argument("--label", required=True)
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--port", type=int, default=8781)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}" if args.isolated else args.base_url
    raise SystemExit(run(base, args.label, args.isolated, args.port))
