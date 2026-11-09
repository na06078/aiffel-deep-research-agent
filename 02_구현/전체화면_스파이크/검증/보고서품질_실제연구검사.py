"""승인된 로컬 실제 연구 1건의 UI·정본·표/SVG·세 형식 검증.

--submit은 실제 Luna 호출을 만들므로 별도 승인 후 한 번만 사용한다.
기존 실행이 있으면 재전송하지 않는다. --run-id는 저장 결과만 다시 검사한다.
"""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import pypdfium2 as pdfium
from playwright.sync_api import sync_playwright
from 실제연구_실사용검사 import QUESTION, get
from 화면개편_후보검사 import close_popup, hit_test
from 화면도우미 import open_replay_from_history, return_to_live

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]


def main(args):
    address = urlsplit(args.base_url)
    assert address.scheme == "http" and address.hostname == "127.0.0.1"
    assert address.port not in {8766, 8768} and address.port is not None
    assert args.expected_unknown >= 0
    assert re.fullmatch(r"[A-Za-z0-9_-]+", args.label)
    data = args.data_dir.resolve()
    assert data.is_relative_to(ROOT / "03_실행데이터") and data.name.startswith("보고서품질_")
    out = ROOT / ".cache/보고서품질_검증/실제연구" / args.label
    assert not out.exists(), "기존 증거는 덮어쓰지 않습니다. 새 label을 사용하세요."
    out.mkdir(parents=True)
    result = {
        "base_url": args.base_url,
        "checks": [],
        "captures": [],
        "requests": [],
        "errors": [],
        "submitted": False,
    }

    def record(name, **facts):
        result["checks"].append({"name": name, "passed": True, **facts})
        print("PASS " + name, flush=True)

    def screenshot(page, name):
        page.screenshot(path=str(out / name))
        result["captures"].append(name)

    initial = get(args.base_url, "/api/spike/snapshot")
    assert initial["provider"] == "openai" and initial["mode"] == "research"
    assert initial["budget"]["max_attempts"] == 185 and initial["budget"]["max_usd"] == 2
    assert initial["budget"]["unknown_count"] == args.expected_unknown
    result["budget_before"] = initial["budget"]
    if args.submit:
        assert args.preflight is not None
        preflight = json.loads(args.preflight.read_text("utf-8"))
        assert preflight["mode"] == "live"
        assert preflight["passed"] and preflight["live_verified"]
        assert preflight["observed_attempts"] == preflight["completed_calls"] == 4
        assert preflight["draft_protocol"] == "short-number-json-schema-v1"
        assert preflight["draft_schema_received"] is True
        assert args.preflight.resolve().is_relative_to(ROOT / ".cache/보고서품질_검증/실모델점검")
        for filename, digest in preflight["source_hashes"].items():
            target = Path(filename).resolve()
            assert target.is_relative_to(ROOT)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        section_input = json.loads((args.preflight.parent / "입력_section.json").read_text("utf-8"))
        schema = section_input["response_schema"]
        assert (
            preflight["draft_schema_sha256"]
            == hashlib.sha256(
                json.dumps(schema, ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
        )
        offered = json.loads(section_input["user"])
        assert schema["properties"]["근거목록ID"]["enum"] == [offered["evidence_set_id"]]
        assert schema["properties"]["문장"]["items"]["properties"]["근거구간"]["items"] == {
            "type": "integer",
            "enum": [span["id"] for span in offered["evidence"]],
        }
        receipts = json.loads((args.preflight.parent / "호출영수증.json").read_text("utf-8"))
        assert [r["case"] for r in receipts] == ["section", "synthesis", "positive", "negative"]
        assert all(r["model"] == "gpt-5.6-luna" and r["request_id"] for r in receipts)
        assert (
            preflight["usage_after"]["attempt_count"] - preflight["usage_before"]["attempt_count"]
            == 4
        )
        assert (
            preflight["usage_before"]["unknown_count"]
            == preflight["usage_after"]["unknown_count"]
            == args.expected_unknown
        )
        assert not initial["runs"] and not initial["queue"], (
            "실행 기록이 있어 새 연구를 전송하지 않습니다."
        )
        assert initial["selected_source_mode"] == "local"
        assert initial["budget"]["remaining_attempts"] >= 55
        assert initial["budget"]["attempt_count"] == preflight["usage_after"]["attempt_count"]
    else:
        assert args.run_id, "조회만 검사하려면 --run-id가 필요합니다."

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(
                    ROOT / ".cache/playwright/chromium-1248/chrome-win64/chrome.exe"
                ),
                headless=True,
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 900}, accept_downloads=True
            )

            def guard(route):
                request = route.request
                if not request.url.startswith(args.base_url + "/"):
                    result["errors"].append("차단한 비로컬 요청: " + request.url)
                    route.abort()
                    return
                if request.method != "GET":
                    command = args.submit and request.url == args.base_url + "/api/spike/commands"
                    export = bool(
                        re.fullmatch(
                            re.escape(args.base_url) + r"/api/spike/reports/[a-f0-9]{32}/exports",
                            request.url,
                        )
                    )
                    if request.method != "POST" or not (command or export):
                        result["errors"].append("미승인 변경 요청: " + request.url)
                        route.abort()
                        return
                result["requests"].append({"method": request.method, "url": request.url})
                route.continue_()

            context.route("**/*", guard)
            page = context.new_page()
            page.set_default_timeout(20000)
            page.on("pageerror", lambda error: result["errors"].append(str(error)))
            page.goto(args.base_url + "/#/control")
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            assert not page.locator(".mock-badge").is_visible()
            screenshot(page, "실제연구_시작.png")
            if args.submit:
                page.get_by_test_id("command-input").fill(QUESTION)
                with page.expect_response(
                    lambda response: (
                        response.url == args.base_url + "/api/spike/commands"
                        and response.request.method == "POST"
                    )
                ) as admitted:
                    page.get_by_test_id("command-submit").click()
                assert admitted.value.status == 202
                admission = admitted.value.json()
                run_id = admission["run_id"]
                result.update(submitted=True, run_id=run_id, admission=admission)
                (out / "접수.json").write_text(
                    json.dumps(admission, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            else:
                run_id = args.run_id
                result["run_id"] = run_id
            last = None
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                snapshot = get(args.base_url, "/api/spike/snapshot")
                run = next(item for item in snapshot["runs"] if item["run_id"] == run_id)
                phase = (run["status"], run["phase"], run["actual_model_requests"])
                if phase != last:
                    print("REAL_STATE " + json.dumps(phase, ensure_ascii=False), flush=True)
                    last = phase
                if run["status"] != "running":
                    break
                page.wait_for_timeout(1500)
            result["run"] = {k: v for k, v in run.items() if k != "draft"}
            result["budget_after_research"] = snapshot["budget"]
            assert run["status"] == "completed", (
                f"원래 실행을 보존합니다: {run['status']}/{run['phase']}"
            )
            assert run["quality"]["passed"] and run["quality"]["semantic_review"]["통과"]
            assert run["source_mode"] == "local" and run["mock_requests"] == 0
            assert 0 < run["actual_model_requests"] <= 55
            assert snapshot["budget"]["unknown_count"] == initial["budget"]["unknown_count"]
            if args.submit:
                assert (
                    snapshot["budget"]["attempt_count"] - initial["budget"]["attempt_count"]
                    == run["actual_model_requests"]
                )
            archive = get(args.base_url, f"/api/spike/runs/{run_id}/archive")
            assert not any(event["type"].startswith("web.") for event in archive["events"])
            responses = [event for event in archive["events"] if event["type"] == "model.completed"]
            assert len(responses) == run["actual_model_requests"]
            assert all(
                event["provider"] == "openai" and event["output"]["model"] == "gpt-5.6-luna"
                for event in responses
            )
            (out / "실제아카이브.json").write_text(
                json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            record(
                "실제 모델·공유 장부·6노드·의미 검토·정본 제출",
                actual_model_requests=run["actual_model_requests"],
            )
            report = get(args.base_url, f"/api/spike/reports/{run['report_id']}")
            result.update(report_id=report["report_id"], report_sha256=report["sha256"])
            page.wait_for_timeout(3000)
            page.get_by_test_id("paper-document").click()
            page.get_by_test_id("report-viewer").wait_for()
            frame = page.frame_locator("iframe.report-presentation-frame")
            frame.locator("h1").wait_for()
            tables = frame.locator("table").count()
            diagrams = frame.locator("svg").count()
            assert tables >= 1 and diagrams >= 1, "이번 비교/연표 주제의 실제 표·도식을 확인합니다."
            assert frame.locator("script,img").count() == 0
            record("종합자의 제출 문서 클릭과 실제 표·코드 도식", tables=tables, diagrams=diagrams)
            downloaded = {}
            for format in ("md", "html", "pdf"):
                if format == "md":
                    link = page.get_by_role("link", name="Markdown 다운로드", exact=True)
                else:
                    create = page.get_by_test_id(f"export-{format}-create")
                    if create.count():
                        create.click()
                    link = page.get_by_test_id(f"export-{format}-download")
                    link.wait_for(timeout=45000)
                with page.expect_download() as download:
                    link.click()
                destination = out / (
                    "🧾 실제 연구 보고서.md" if format == "md" else f"실제_연구보고서.{format}"
                )
                download.value.save_as(str(destination))
                content = destination.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                if format == "md":
                    assert (
                        digest == report["sha256"] and content.decode("utf-8") == report["markdown"]
                    )
                elif format == "html":
                    assert "<table" in content.decode("utf-8") and "<svg" in content.decode("utf-8")
                    opened = context.new_page()
                    opened.set_content(content.decode("utf-8"))
                    assert opened.locator("table").count() == tables
                    assert opened.locator("svg").count() == diagrams
                    opened.locator("svg").first.scroll_into_view_if_needed()
                    screenshot(opened, "HTML_실제파일_재열기.png")
                    opened.close()
                else:
                    with pdfium.PdfDocument(content) as pdf:
                        text = []
                        for number in range(len(pdf)):
                            text_page = pdf[number].get_textpage()
                            text.append(text_page.get_text_range())
                            pdf[number].render(scale=1.25).to_pil().save(
                                out / f"PDF_페이지_{number + 1}.png"
                            )
                        rendered_title = frame.locator("h1").first.inner_text()
                        assert "".join(rendered_title.split()) in "".join("".join(text).split())
                        result["pdf_pages"] = len(pdf)
                if format != "md":
                    exports = get(
                        args.base_url, f"/api/spike/reports/{report['report_id']}/exports"
                    )["exports"]
                    entry = next(item for item in exports if item["format"] == format)
                    assert (
                        entry["sha256"] == digest
                        and entry["source_report_hash"] == report["sha256"]
                    )
                downloaded[format] = {
                    "file": str(destination),
                    "bytes": len(content),
                    "sha256": digest,
                }
            result["downloads"] = downloaded
            record(
                "Markdown·HTML·PDF 생성·다운로드·실제 파일 재열기", pdf_pages=result["pdf_pages"]
            )
            for width, height in ((1440, 900), (390, 844), (844, 390)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(250)
                iframe = page.locator("iframe.report-presentation-frame")
                # parent02에서 검증한 외부 팝업/내부 iframe의 실제 휠 경로를 사용한다.
                # 중첩 scroll_into_view_if_needed는 작은 높이에서 완전 노출을 기다리며 막힌다.
                iframe.evaluate(
                    """el=>{const root=el.closest('.popup-body');const a=el.getBoundingClientRect(),b=root.getBoundingClientRect();root.scrollTop+=a.top-b.top-4;}"""
                )
                inside = page.frames[1]
                inside.evaluate("window.scrollTo(0,0)")
                label = frame.locator("svg text").first
                assert label.count() > 0
                reached = False
                for _ in range(40):
                    box = label.bounding_box()
                    outer = iframe.bounding_box()
                    clip = page.locator(".popup-body").bounding_box()
                    top = max(outer["y"], clip["y"])
                    bottom = min(outer["y"] + outer["height"], clip["y"] + clip["height"], height)
                    if box and box["y"] >= top and box["y"] + box["height"] <= bottom:
                        reached = True
                        break
                    page.mouse.move(outer["x"] + outer["width"] / 2, (top + bottom) / 2)
                    page.mouse.wheel(0, 180)
                    page.wait_for_timeout(60)
                assert reached, "보이는 iframe 영역에서 실제 도식 레이블에 접근해야 합니다."
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                assert page.locator("canvas").count() == 1
                close = hit_test(page.get_by_test_id("popup-close"))
                assert close["hit"] and close["inView"]
                screenshot(page, f"실제_도식_{width}x{height}.png")
                record(f"실제 보고서 viewport {width}x{height}", close=close)
            page.set_viewport_size({"width": 1440, "height": 900})
            close_popup(page)
            before = get(args.base_url, "/api/spike/snapshot")["budget"]
            database = data / "실행기록.sqlite3"
            before_hash = hashlib.sha256(database.read_bytes()).hexdigest()
            open_replay_from_history(page, run_id)
            slider = page.get_by_test_id("timeline-scrubber")
            slider.focus()
            slider.press("Home")
            assert not page.get_by_test_id("paper-document").is_visible()
            slider.press("End")
            page.reload()
            page.wait_for_selector('[data-testid="world"][data-render-ready="true"]')
            return_to_live(page)
            after = get(args.base_url, "/api/spike/snapshot")["budget"]
            assert (
                before == after and hashlib.sha256(database.read_bytes()).hexdigest() == before_hash
            )
            record("실제 기록 재생·되감기·새로고침의 무재호출·DB 불변")
            assert not result["errors"]
            result["passed"] = True
            context.close()
            browser.close()
    except Exception as error:  # noqa: BLE001 - 기존 실행/원시 출력은 보존하고 실패만 기록한다.
        result.update(passed=False, error=str(error))
    finally:
        (out / "확인.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "evidence": str(out / "확인.json"),
                    "passed": result.get("passed"),
                    "run_id": result.get("run_id"),
                    "checks": len(result["checks"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--preflight", type=Path)
    parser.add_argument(
        "--expected-unknown",
        type=int,
        default=0,
        help="별도 확인/보존하는 과거 미정산 예약 수(새 실패를 허용하는 값 아님)",
    )
    raise SystemExit(main(parser.parse_args()))
