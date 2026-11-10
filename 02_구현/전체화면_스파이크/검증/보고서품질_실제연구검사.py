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


def validate_preflight(path, expected_unknown):
    """submit 전에 로컬 증거만 읽는다. 제공자·장부·HTTP·브라우저 I/O는 없다."""
    from 보고서품질_실모델점검 import CONTROL_PROTOCOL, control_cases, inputs, manifest
    from 보고서품질_실모델점검 import ROOT as PROBE_ROOT

    from 서버.검토문맥 import build_review_request, parse_review
    from 서버.교안연구 import assemble_report
    from 서버.보고서근거 import build_draft_request, materialize_section
    from 서버.보고서품질 import parse_synthesis, visible_markdown

    try:
        assert path is not None
        path = path.resolve()
        cache_root = (PROBE_ROOT / ".cache/보고서품질_검증/실모델점검").resolve()
        assert path.is_relative_to(cache_root)

        def read(name):
            target = (path.parent / name).resolve()
            assert target.is_relative_to(cache_root)
            return json.loads(target.read_text("utf-8"))

        value = read(path.name)
        provenance, _, _ = control_cases(inputs()[0])
        assert value.get("control_protocol") == CONTROL_PROTOCOL
        assert value.get("control_source") == provenance
        assert value.get("generated_candidate_semantic_verified") is False
        assert value["mode"] == "live"
        assert value["passed"] and value["live_verified"]
        assert value["observed_attempts"] == value["completed_calls"] == 4
        assert value["draft_protocol"] == "sentence-paragraph-evidence-v3"
        assert value["review_protocol"] == "source-context-review-v1"
        assert value["review_schema_cases"] == ["positive", "negative"]
        assert value["draft_schema_received"] is True
        assert value["source_hashes"] == manifest()
        section_input = read("입력_section.json")
        schema = section_input["response_schema"]
        assert (
            value["draft_schema_sha256"]
            == hashlib.sha256(
                json.dumps(schema, ensure_ascii=False, allow_nan=False).encode("utf-8")
            ).hexdigest()
        )
        offered = json.loads(section_input["user"])
        assert schema["properties"]["근거목록ID"]["enum"] == [offered["evidence_set_id"]]
        assert schema["properties"]["문장"]["items"] == {"type": "string"}
        assert schema["properties"]["근거관계"]["maxItems"] == 16
        assert schema["properties"]["근거관계"]["items"]["properties"]["근거구간"] == {
            "type": "integer",
            "enum": [span["id"] for span in offered["evidence"]],
        }
        receipts = read("호출영수증.json")
        assert [r["case"] for r in receipts] == ["section", "synthesis", "positive", "negative"]
        assert all(r["model"] == "gpt-5.6-luna" and r["request_id"] for r in receipts)
        assert value["usage_after"]["attempt_count"] - value["usage_before"]["attempt_count"] == 4
        assert (
            value["usage_before"]["unknown_count"]
            == value["usage_after"]["unknown_count"]
            == expected_unknown
        )
        assert value["negative_detected"] is True
        assert value["passed"] is True and value["live_verified"] is True
        corpus, data = inputs()
        sources = {"임오군란": corpus.docs["임오군란"]}
        draft_request = build_draft_request(data["assignment"], data["notes"], sources)
        assert section_input["response_schema"] == draft_request.response_schema
        assert (section_input["system"], section_input["user"]) == draft_request.prompts
        generated = materialize_section(
            read("응답_section.json")["content"], draft_request, sources
        )
        assert generated["충분"] and "1882년" in visible_markdown(generated["본문"])
        section = {**data["assignment"], **generated, "읽은문서": ["임오군란"]}
        assert read("절_조립.json") == {"section": section, "request_inputs": draft_request.inputs}
        summary = parse_synthesis(read("응답_synthesis.json")["content"], sources)
        assert summary["표"] and summary["도식"]
        candidate = assemble_report(
            "임오군란 발생 시기 사전점검",
            summary["머리말"],
            summary["맺음말"],
            [section],
            corpus,
            summary,
        )
        candidates = read("후보.json")
        assert candidates["positive"] == candidate
        assert candidates["negative"] == candidate.replace(
            section["본문"], section["본문"].replace("1882년", "1883년", 1), 1
        )
        assert candidates["evidence"] == [
            {"section_id": section["section_id"], "근거": section["근거"]}
        ]
        provenance, cases, qualified_sources = control_cases(corpus)
        controls = read("대조.json")
        assert controls == {
            "control_protocol": CONTROL_PROTOCOL,
            "control_source": provenance,
            "generated_candidate_semantic_verified": False,
            "positive": cases["positive"]["draft"],
            "negative": cases["negative"]["draft"],
            "sections": cases["positive"]["sections"],
            "negative_sections": cases["negative"]["sections"],
        }
        for case in ("positive", "negative"):
            control = cases[case]
            request = build_review_request(control["draft"], control["sections"], qualified_sources)
            saved = read(f"입력_{case}.json")
            assert saved["request_inputs"] == request.inputs
            assert saved["response_schema"] == request.response_schema
            assert (saved["system"], saved["user"]) == request.prompts
            verdict = parse_review(read(f"응답_{case}.json")["content"], request, qualified_sources)
            assert verdict["통과"] is (case == "positive")
            if case == "negative":
                assert any(
                    p["유형"] == "contradiction"
                    and "1883년" in p["본문발췌"]
                    and p["본문발췌"] in request.inputs["visible_text"]
                    and "임오군란" in p["출처"]
                    for p in verdict["문제"]
                )
        attempts = read("시도.json")
        assert attempts == [{"case": r["case"], "attempt_id": r["attempt_id"]} for r in receipts]
        assert (
            len({r["attempt_id"] for r in receipts})
            == len({r["request_id"] for r in receipts})
            == 4
        )
        assert all(
            re.fullmatch(r"[a-f0-9]{32}", r["attempt_id"])
            and re.fullmatch(r"req_[A-Za-z0-9_-]+", r["request_id"])
            and isinstance(r["usage"], dict)
            and all(
                type(r["usage"][k]) is int and r["usage"][k] >= 0
                for k in ("input_tokens", "output_tokens")
            )
            for r in receipts
        )
        operations = {
            "section": "draft_section",
            "synthesis": "synthesize",
            "positive": "semantic_review",
            "negative": "semantic_review",
        }
        for case, operation in operations.items():
            saved = read(f"입력_{case}.json")
            assert saved["context"] == {
                "run_id": path.parent.name,
                "revision": 1,
                "actor": "quality-preflight",
                "operation": operation,
                "case": case,
            }
            assert saved["max_output_tokens"] == 4096 and saved["json_mode"] is True
        return value
    except (AssertionError, KeyError, TypeError, ValueError, OSError) as error:
        raise AssertionError("preflight_control_invalid") from error


def main(args):
    address = urlsplit(args.base_url)
    assert address.scheme == "http" and address.hostname == "127.0.0.1"
    assert address.port not in {8766, 8768} and address.port is not None
    assert args.expected_unknown >= 0
    assert re.fullmatch(r"[A-Za-z0-9_-]+", args.label)
    data = args.data_dir.resolve()
    assert data.is_relative_to(ROOT / "03_실행데이터") and data.name.startswith("보고서품질_")
    preflight = validate_preflight(args.preflight, args.expected_unknown) if args.submit else None
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
    assert (
        initial["budget"]["max_attempts"] == args.expected_max_attempts
        and initial["budget"]["max_usd"] == args.expected_max_usd
    ), "공유 장부의 호출/금액 한도가 승인값과 다릅니다."
    assert initial["budget"]["unknown_count"] == args.expected_unknown
    result["budget_before"] = initial["budget"]
    if args.submit:
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
        "--expected-max-attempts",
        type=int,
        default=208,
        help="승인된 기존 공유 장부의 누적 호출 상한",
    )
    parser.add_argument(
        "--expected-max-usd",
        type=int,
        default=2,
        help="승인된 기존 공유 장부의 누적 추정 달러 상한",
    )
    parser.add_argument(
        "--expected-unknown",
        type=int,
        default=0,
        help="별도 확인/보존하는 과거 미정산 예약 수(새 실패를 허용하는 값 아님)",
    )
    raise SystemExit(main(parser.parse_args()))
