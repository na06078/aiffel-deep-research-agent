"""실제 자료 사본과 격리 서버에서 실행 삭제를 브라우저로 검증합니다."""

import argparse
import hashlib
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from 삭제_사본만들기 import COPIES, ROOT, counts, make_copy, readonly
from 화면개편_후보검사 import (
    close_popup,
    close_timeline,
    hit_test,
    open_history_popup,
    open_timeline,
)
from 화면도우미 import open_replay_from_history

SPIKE = Path(__file__).resolve().parents[1]
VER = Path(__file__).resolve().parent
FE = SPIKE / "화면"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")
# 한국어 UI 문구는 제품 소스에서 추출합니다.
UI = (FE / "src/실행삭제.tsx").read_text(encoding="utf-8")
LOGIC = (FE / "src/실행삭제.ts").read_text(encoding="utf-8")
LIST = (FE / "src/과거기록목록.tsx").read_text(encoding="utf-8")
APP = (FE / "src/앱.tsx").read_text(encoding="utf-8")
TIMELINE = (FE / "src/실행타임라인.tsx").read_text(encoding="utf-8")
RUNNING = re.search("const runningTitle='([^']+)'", UI)[1]
NOTICE = re.search("return \\['([^']+)'", LOGIC)[1]
EMPTY = re.search(r"if\(!props.runs.length\)return <p>(.*?)</p>", LIST)[1]
NO_RUN = re.search(r"!p.runs.length&&!p.loading\?'([^']+)'", TIMELINE)[1]
DETAIL = re.search(r"<button onClick=\{\(\)=>props.onDetail\(run.run_id\)\}>(.*?)</button>", LIST)[
    1
]
EVENT_MENU = re.search(r"ui.popup.kind==='([^']+)'&&<>\{eventList", APP)[1]
SCOPE_TEMPLATE = re.search(r"return `([^`]+)`;", LOGIC[LOGIC.index("export function scopeText") :])[
    1
]


def get(base, path):
    """GET 전용 API 조회입니다."""
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.load(r)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fingerprint():
    s = get("http://127.0.0.1:8768", "/api/spike/snapshot")
    return {
        "runs": sorted((r["run_id"], r["status"], r["updated_at"]) for r in s["runs"]),
        "attempt_count": s["budget"]["attempt_count"],
        "high_watermark_seq": s["high_watermark_seq"],
    }


def db_rows(folder, rid):
    """각 실행의 직접 행과 JSON 귀속 행을 독립 집계합니다."""
    result = {}
    with closing(readonly(folder / "실행기록.sqlite3")) as db:
        for table in ("revisions", "calls", "activity_spans"):
            result[table] = db.execute(
                f"SELECT count(*) FROM {table} WHERE run_id=?", (rid,)
            ).fetchone()[0]
        for table in ("events", "reports"):
            result[table] = db.execute(
                f"SELECT count(*) FROM {table} WHERE json_extract(body,'$.run_id')=?", (rid,)
            ).fetchone()[0]
        result["requests"] = db.execute(
            "SELECT count(*) FROM requests WHERE json_extract(body,'$.run_id')=? OR json_extract(response,'$.run_id')=?",
            (rid, rid),
        ).fetchone()[0]
    web = folder / "웹자료/자료.sqlite3"
    for table in ("web_sources", "web_reads"):
        with closing(readonly(web)) as db:
            result[table] = db.execute(
                f"SELECT count(*) FROM {table} WHERE run_id=?", (rid,)
            ).fetchone()[0]
    return result


def assets(folder, rid):
    with closing(readonly(folder / "실행기록.sqlite3")) as db:
        reports = db.execute(
            "SELECT report_id,filename FROM reports WHERE json_extract(body,'$.run_id')=?", (rid,)
        ).fetchall()
    ids = {r[0] for r in reports}
    files = [folder / r[1] for r in reports if (folder / r[1]).is_file()]
    exports = []
    for p in (folder / "형식변환").glob("*.json"):
        if json.loads(p.read_text(encoding="utf-8")).get("report_id") in ids:
            exports.extend(
                q for q in (p, p.with_suffix(".html"), p.with_suffix(".pdf")) if q.is_file()
            )
    return {
        "reports": {str(p.relative_to(folder)): sha(p) for p in files},
        "exports": {str(p.relative_to(folder)): sha(p) for p in exports},
    }


def all_counts(folder):
    result = {}
    for p in folder.rglob("*.sqlite3"):
        with closing(readonly(p)) as db:
            result[str(p.relative_to(folder))] = counts(db)
    return result


def tombstones(folder):
    with closing(readonly(folder / "실행기록.sqlite3")) as db:
        return [
            json.loads(r[0])
            for r in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.type')='run.deleted'"
            )
        ]


def deletion_receipt_ok(rows, run_id):
    """28.3.5의 삭제 응답 한 행만 허용한다. 원래 지시·초안이 남으면 실패다."""
    if len(rows) != 1 or rows[0]["body"] != {"action": "delete", "run_id": run_id}:
        return False
    response = rows[0]["response"]
    return (
        set(response) == {"status", "run_id", "counts", "warnings", "current_run_id"}
        and response["status"] == "deleted"
        and response["run_id"] == run_id
        and response["warnings"] == []
        and set(response["counts"])
        == {
            "events",
            "calls",
            "activity_spans",
            "requests",
            "reports",
            "report_files",
            "export_files",
            "web_sources",
            "web_reads",
        }
        and all(type(n) is int and n >= 0 for n in response["counts"].values())
        and (response["current_run_id"] is None or isinstance(response["current_run_id"], str))
    )


def request_rows(folder, run_id):
    with closing(readonly(folder / "실행기록.sqlite3")) as db:
        rows = db.execute(
            "SELECT body,response FROM requests WHERE json_extract(body,'$.run_id')=?"
            " OR json_extract(response,'$.run_id')=?",
            (run_id, run_id),
        )
        return [{"body": json.loads(row[0]), "response": json.loads(row[1])} for row in rows]


def web_source_ids(folder, run_id):
    with closing(readonly(folder / "웹자료/자료.sqlite3")) as db:
        return [
            row[0]
            for row in db.execute("SELECT source_id FROM web_sources WHERE run_id=?", (run_id,))
        ]


def history_count_for_ids(folder, source_ids):
    # 출처를 지운 뒤의 빈 하위 쿼리로 검사하면 고아 이력도 통과하므로 선조회 ID를 쓴다.
    with closing(readonly(folder / "웹자료/자료.sqlite3")) as db:
        return sum(
            db.execute("SELECT count(*) FROM web_history WHERE source_id=?", (sid,)).fetchone()[0]
            for sid in source_ids
        )


@contextmanager
def server(args, out, group, copies):
    """새 사본 서버만 시작하고 소유한 프로세스만 종료합니다."""
    dest = COPIES / (datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + group)
    summary = make_copy(dest)
    copies.append(summary)
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError(f"포트 사용 중: {args.port}")
    command = [
        str(ROOT / ".venv/Scripts/python.exe"),
        str(VER / "웹연구_격리서버.py"),
        "--port",
        str(args.port),
        "--data-dir",
        str(dest),
        "--ledger",
        str(dest / "호출예산.sqlite3"),
        "--static",
        str(args.static),
    ]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        cwd=str(ROOT),
    )
    lines, q = [], queue.Queue()

    def reader():
        for line in proc.stdout:
            lines.append(line.rstrip())
            q.put(line)

    threading.Thread(target=reader, daemon=True).start()
    base = f"http://127.0.0.1:{args.port}"
    try:
        deadline = time.time() + 45
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("서버 기동 실패: " + repr(lines))
            try:
                if q.get(timeout=0.2).startswith("READY "):
                    ready = True
            except queue.Empty:
                pass
            if ready:
                try:
                    get(base, "/api/health")
                    break
                except (urllib.error.URLError, ConnectionError, TimeoutError) as error:
                    lines.append(f"health 대기: {type(error).__name__}")
        else:
            raise RuntimeError("서버 READY/health 시간 초과: " + repr(lines))
        yield dest, base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        (out / (group + "_서버.json")).write_text(
            json.dumps(
                {"pid": proc.pid, "command": command, "log": lines, "returncode": proc.returncode},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def main(args):
    if args.port in {8766, 8768, 8790, 8791}:
        raise ValueError("금지 포트")
    out = (SPIKE / "검증결과/삭제" / args.label).resolve()
    if not out.is_relative_to((SPIKE / "검증결과/삭제").resolve()):
        raise ValueError("증거 경로")
    out.mkdir(parents=True, exist_ok=True)
    checks, shots, copies, requests, errors, denied, layouts = [], [], [], [], [], [], []
    before_live = fingerprint()
    manifest = json.loads(
        (ROOT / ".cache/화면개편_후보_매니페스트.json").read_text(encoding="utf-8")
    )["files"]

    def dist():
        return {
            str(p.relative_to(FE / "dist")).replace("\\", "/"): sha(p)
            for p in (FE / "dist").rglob("*")
            if p.is_file()
        }

    before_dist = dist()

    def add(name, data):
        failures = [k for k, v in data.items() if k.endswith("_ok") and not v]
        checks.append(
            {
                "scenario": name,
                "status": "FAIL" if failures else "PASS",
                "failed_checks": failures,
                "measured": data,
            }
        )
        print(name, checks[-1]["status"], json.dumps(data, ensure_ascii=False), flush=True)

    def scenario(name, func, page):
        try:
            data = func()
            add(name, data)
            if checks[-1]["status"] == "FAIL":
                path = out / (name + "_실패.png")
                page.screenshot(path=str(path))
                shots.append(str(path))
                checks[-1]["screenshot"] = str(path)
        except Exception as exc:  # noqa: BLE001 -- 실패를 기록하고 다음 시나리오도 검사한다.
            path = out / (name + "_실패.png")
            try:
                page.screenshot(path=str(path))
                shots.append(str(path))
            except PlaywrightError as shot_error:
                errors.append({"type": "screenshot", "text": str(shot_error)})
            checks.append(
                {
                    "scenario": name,
                    "status": "FAIL",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "screenshot": str(path),
                }
            )
            print(name, "FAIL", str(exc), flush=True)

    def page_new(browser, base, rewrite=None):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        p = ctx.new_page()
        p.set_default_timeout(12000)
        p.on("request", lambda r: requests.append((r.method, r.url)))
        p.on(
            "console",
            lambda m: (
                errors.append({"type": "console", "text": m.text, "location": m.location})
                if m.type == "error"
                else None
            ),
        )
        p.on("pageerror", lambda e: errors.append({"type": "pageerror", "text": str(e)}))

        def guard(route):
            r = route.request
            allowed = r.url.startswith(base + "/") and (
                r.method == "GET"
                or (
                    r.method == "POST"
                    and re.fullmatch(re.escape(base) + r"/api/spike/runs/[0-9a-f]+/delete", r.url)
                )
            )
            if not allowed:
                denied.append((r.method, r.url))
                route.abort()
                return
            if rewrite and r.method == "GET" and "/api/spike/snapshot" in r.url:
                resp = route.fetch()
                body = resp.json()
                rewrite(body)
                route.fulfill(response=resp, json=body)
            else:
                route.continue_()

        p.route("**/*", guard)
        p.goto(base + "/#/control")
        p.get_by_test_id("menu-toggle").wait_for()
        return ctx, p

    def item(p, rid):
        return p.locator(f'[data-testid="history-item"][data-run-id="{rid}"]')

    def preview(p, rid):
        with p.expect_response(lambda r: r.url.endswith("/" + rid + "/delete-preview")) as response:
            item(p, rid).get_by_test_id("history-delete").click()
        body = response.value.json()
        p.get_by_test_id("history-delete-cancel").wait_for()
        return body

    def layout(p, state):
        for w, h in [(1440, 900), (390, 844)]:
            p.set_viewport_size({"width": w, "height": h})
            p.wait_for_timeout(150)
            block = p.get_by_test_id("history-delete-confirm")
            buttons = []
            if block.count():
                for b in block.locator("button").all():
                    b.scroll_into_view_if_needed()
                    buttons.append(hit_test(b))
            m = p.evaluate("()=>({sw:document.documentElement.scrollWidth,cw:innerWidth})")
            m.update({"state": state, "viewport": [w, h], "buttons": buttons})
            m["block"] = (
                block.evaluate("(e)=>({sw:e.scrollWidth,cw:e.clientWidth})")
                if block.count()
                else None
            )
            m["layout_ok"] = (
                m["sw"] <= m["cw"]
                and all(b["hit"] and b["inView"] for b in buttons)
                and (not m["block"] or m["block"]["sw"] <= m["block"]["cw"])
            )
            file = out / f"{state}_{w}x{h}.png"
            p.screenshot(path=str(file))
            shots.append(str(file))
            layouts.append(m)
        p.set_viewport_size({"width": 1440, "height": 900})

    def delete_check(p, base, folder, run, remaining):
        rid = run["run_id"]
        before = get(base, "/api/spike/snapshot")
        rows = db_rows(folder, rid)
        files = assets(folder, rid)
        source_ids = web_source_ids(folder, rid)
        history_before = history_count_for_ids(folder, source_ids)
        ledger = sha(folder / "호출예산.sqlite3")
        tomb_before = len(tombstones(folder))
        own = {k: v for k, v in rows.items() if k != "revisions"}
        own.update(report_files=len(files["reports"]), export_files=len(files["exports"]))
        pr = preview(p, rid)
        expected = re.sub(r"\$\{counts\.(\w+)\}", lambda m: str(own[m[1]]), SCOPE_TEMPLATE)
        scope = p.locator(".delete-scope").inner_text()
        layout(p, "ready_" + rid[:6])
        with p.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/" + rid + "/delete")
        ) as response:
            p.get_by_test_id("history-delete-yes").click()
        result = response.value.json()
        item(p, rid).wait_for(state="detached")
        p.wait_for_timeout(300)
        after = get(base, "/api/spike/snapshot")
        after_rows = db_rows(folder, rid)
        tomb = tombstones(folder)
        try:
            get(base, "/api/spike/runs/" + rid + "/archive")
            archive = 200
        except urllib.error.HTTPError as e:
            archive = e.code
        request_evidence = request_rows(folder, rid)
        data = {
            "request_rows_evidence": request_evidence,
            "tombstone_last": tomb[-1] if tomb else None,
            "counts_before": own,
            "preview_counts": pr["counts"],
            "counts_ok": own == pr["counts"],
            "scope_text": scope,
            "scope_ok": expected in scope,
            "delete_response": result,
            "remaining": len(after["runs"]),
            "remaining_ok": len(after["runs"]) == remaining,
            "rows_after": after_rows,
            "content_rows_zero_ok": all(v == 0 for k, v in after_rows.items() if k != "requests"),
            "delete_receipt_ok": after_rows["requests"] == 1
            and deletion_receipt_ok(request_evidence, rid),
            "web_source_ids_before": source_ids,
            "web_history_before": history_before,
            "web_history_after": history_count_for_ids(folder, source_ids),
            "web_history_gone_ok": history_count_for_ids(folder, source_ids) == 0,
            "tombstones_before": tomb_before,
            "tombstones_after": len(tomb),
            "tombstone_ok": len(tomb) == tomb_before + 1
            and tomb[-1]["run_id"] == "deletions"
            and run["question"][:20] not in json.dumps(tomb[-1], ensure_ascii=False),
            "watermark_before": before["high_watermark_seq"],
            "watermark_after": after["high_watermark_seq"],
            "watermark_ok": after["high_watermark_seq"] > before["high_watermark_seq"],
            "ledger_sha_before": ledger,
            "ledger_sha_after": sha(folder / "호출예산.sqlite3"),
            "ledger_ok": ledger == sha(folder / "호출예산.sqlite3"),
            "archive_status": archive,
            "archive_ok": archive == 404,
            "files_before": files,
            "files_gone_ok": all(
                not (folder / f).exists() for group in files.values() for f in group
            ),
            "notice_ok": p.get_by_text(re.compile("^" + re.escape(NOTICE))).count() > 0,
        }
        return data

    fatal = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            with server(args, out, "S1_S4", copies) as (folder, base):
                ctx, p = page_new(browser, base)
                snap = get(base, "/api/spike/snapshot")
                runs = snap["runs"]
                a = next(
                    r
                    for r in runs
                    if r["status"] == "completed" and r.get("source_mode", "local") == "local"
                )
                b = next(r for r in runs if r["status"] == "blocked")
                c = next(
                    r for r in runs if r.get("source_mode") == "web" and r["status"] == "completed"
                )
                open_history_popup(p)

                def s1():
                    before = all_counts(folder)
                    preview(p, a["run_id"])
                    text = p.get_by_test_id("history-delete-blocked").inner_text()
                    data = {
                        "dependent_count": sum(r["question"] in text for r in (b, c)),
                        "dependents_ok": all(r["question"] in text for r in (b, c)),
                        "confirm_yes_count": p.get_by_test_id("history-delete-yes").count(),
                        "confirm_absent_ok": p.get_by_test_id("history-delete-yes").count() == 0,
                        "blocked_text": text,
                    }
                    layout(p, "blocked")
                    p.get_by_test_id("history-delete-cancel").click()
                    data.update(
                        runs=len(get(base, "/api/spike/snapshot")["runs"]),
                        db_before=before,
                        db_after=all_counts(folder),
                        unchanged_ok=before == all_counts(folder),
                        cancel_ok=p.get_by_test_id("history-delete-confirm").count() == 0,
                    )
                    return data

                scenario("S1", s1, p)
                scenario("S2", lambda: delete_check(p, base, folder, b, 2), p)
                layout(p, "after_delete")

                def s3():
                    a_rows = db_rows(folder, a["run_id"])
                    a_files = assets(folder, a["run_id"])
                    d = delete_check(p, base, folder, c, 1)
                    d.update(
                        parent_rows_before=a_rows,
                        parent_rows_after=db_rows(folder, a["run_id"]),
                        parent_ok=a_rows == db_rows(folder, a["run_id"])
                        and a_files == assets(folder, a["run_id"]),
                    )
                    close_popup(p)
                    open_timeline(p)
                    p.wait_for_function(
                        "(rid)=>document.querySelector('[data-testid=\"timeline-subject\"]')?.dataset.runId===rid",
                        arg=a["run_id"],
                    )
                    d.update(
                        current_run_id=get(base, "/api/spike/snapshot")["current_run_id"],
                        current_ok=get(base, "/api/spike/snapshot")["current_run_id"]
                        == a["run_id"],
                        subject=p.get_by_test_id("timeline-subject").inner_text(),
                    )
                    close_timeline(p)
                    open_history_popup(p)
                    return d

                scenario("S3", s3, p)

                def s4():
                    d = delete_check(p, base, folder, a, 0)
                    d.update(
                        empty_ok=EMPTY in p.get_by_test_id("popup").inner_text(),
                        current_ok=get(base, "/api/spike/snapshot")["current_run_id"] is None,
                    )
                    layout(p, "empty")
                    close_popup(p)
                    open_timeline(p)
                    p.wait_for_timeout(500)
                    d.update(
                        subject=p.get_by_test_id("timeline-subject").inner_text(),
                        subject_ok=NO_RUN in p.get_by_test_id("timeline-subject").inner_text(),
                        all_run_rows={r["run_id"]: db_rows(folder, r["run_id"]) for r in runs},
                    )
                    d["all_run_rows_ok"] = all(
                        v == 0
                        for row in d["all_run_rows"].values()
                        for key, v in row.items()
                        if key != "requests"
                    )
                    d["all_delete_receipts_ok"] = all(
                        deletion_receipt_ok(request_rows(folder, r["run_id"]), r["run_id"])
                        for r in runs
                    )
                    return d

                scenario("S4", s4, p)
                # 사건 목록에는 삭제 흔적이 표시되지 않아야 합니다.
                close_popup(p)
                close_timeline(p)
                p.get_by_test_id("menu-toggle").click()
                p.get_by_test_id("drawer").get_by_role(
                    "button", name=EVENT_MENU, exact=True
                ).click()
                text = p.get_by_test_id("popup").inner_text()
                add(
                    "사건목록",
                    {
                        "tombstone_visible_count": text.count("run.deleted"),
                        "hidden_ok": "run.deleted" not in text
                        and not any(t["summary"] in text for t in tombstones(folder)),
                    },
                )
                ctx.close()
            with server(args, out, "S5", copies) as (folder, base):
                ctx, p = page_new(browser, base)
                runs = get(base, "/api/spike/snapshot")["runs"]
                b = next(r for r in runs if r["status"] == "blocked")
                c = next(r for r in runs if r.get("source_mode") == "web")

                def s5():
                    open_replay_from_history(p, c["run_id"])
                    close_timeline(p)
                    open_history_popup(p)
                    db = delete_check(p, base, folder, b, 2)
                    close_popup(p)
                    open_timeline(p)
                    kept = (
                        p.get_by_test_id("timeline-subject").get_attribute("data-mode") == "replay"
                        and p.get_by_test_id("timeline-subject").get_attribute("data-run-id")
                        == c["run_id"]
                    )
                    close_timeline(p)
                    open_history_popup(p)
                    item(p, c["run_id"]).get_by_role("button", name=DETAIL, exact=True).click()
                    # 상세에서도 같은 확인 구성요소를 사용합니다.
                    with p.expect_response(
                        lambda r: r.url.endswith("/" + c["run_id"] + "/delete-preview")
                    ):
                        p.get_by_test_id("popup").get_by_test_id("history-delete").click()
                    p.get_by_test_id("history-delete-yes").click()
                    p.get_by_test_id("history-item").first.wait_for()
                    close_popup(p)
                    open_timeline(p)
                    p.wait_for_function(
                        '()=>document.querySelector(\'[data-testid="timeline-subject"]\')?.dataset.mode==="live"'
                    )
                    stored = p.evaluate('sessionStorage.getItem("village-record-replay")')
                    return {
                        "other_delete": db,
                        "other_delete_ok": all(v for k, v in db.items() if k.endswith("_ok")),
                        "other_replay_preserved_ok": kept,
                        "mode": p.get_by_test_id("timeline-subject").get_attribute("data-mode"),
                        "session_value": stored,
                        "session_removed_ok": stored is None,
                        "detail_return_list_ok": True,
                    }

                scenario("S5", s5, p)
                ctx.close()
            with server(args, out, "S6", copies) as (folder, base):
                rid = get(base, "/api/spike/snapshot")["runs"][0]["run_id"]

                def rewrite(s):
                    for r in s["runs"]:
                        if r["run_id"] == rid:
                            r["status"] = "running"

                ctx, p = page_new(browser, base, rewrite)
                open_history_popup(p)

                def s6():
                    btn = item(p, rid).get_by_test_id("history-delete")
                    n = sum("/delete-preview" in u for m, u in requests)
                    btn.evaluate("(e)=>e.click()")
                    p.wait_for_timeout(400)
                    after = sum("/delete-preview" in u for m, u in requests)
                    return {
                        "disabled_ok": btn.is_disabled(),
                        "title": btn.get_attribute("title"),
                        "title_ok": btn.get_attribute("title") == RUNNING,
                        "preview_requests_before": n,
                        "preview_requests_after": after,
                        "no_preview_ok": n == after,
                    }

                scenario("S6", s6, p)
                ctx.close()
            browser.close()
    except Exception as exc:  # noqa: BLE001 -- 치명 오류도 JSON과 traceback으로 보존한다.
        fatal = {"error": str(exc), "traceback": traceback.format_exc()}
    after_live = fingerprint()
    after_dist = dist()
    add(
        "S7",
        {
            "measurements": layouts,
            "measurement_count": len(layouts),
            "layout_ok": len(layouts) >= 8 and all(m["layout_ok"] for m in layouts),
        },
    )
    nonget = Counter(u for m, u in requests if m != "GET")
    add(
        "보호검사",
        {
            "request_count": len(requests),
            "non_get": dict(nonget),
            "denied": denied,
            "requests_ok": not denied,
            "errors": errors,
            "console_error_count": len(errors),
            "errors_ok": not errors,
            "live_before": before_live,
            "live_after": after_live,
            "live_ok": before_live == after_live,
            "dist_files": len(after_dist),
            "dist_unchanged_ok": before_dist == after_dist,
            "manifest_ok": manifest == after_dist,
        },
    )
    result = {
        "checks": checks,
        "fatal": fatal,
        "copies": copies,
        "screenshots": shots,
        "requests": requests,
        "unverified": [] if not fatal else ["중단 이후 시나리오"],
        "script_sha256": {str(p): sha(p) for p in (Path(__file__), VER / "삭제_사본만들기.py")},
        "command": subprocess.list2cmdline([sys.executable, *sys.argv]),
        "evidence": str(out / "확인.json"),
    }
    (out / "확인.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("RESULT", out / "확인.json", flush=True)
    if fatal:
        print("FATAL", fatal, flush=True)
    return int(bool(fatal) or any(c["status"] == "FAIL" for c in checks))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", type=Path, default=ROOT / ".cache/화면삭제_후보")
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--label", default="삭제/후보_실사용")
    raise SystemExit(main(parser.parse_args()))
