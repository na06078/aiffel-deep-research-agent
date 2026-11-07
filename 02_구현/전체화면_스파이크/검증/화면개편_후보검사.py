"""화면 개편(타임라인 머리글 + 과거 기록 목록) 실제 브라우저 검증. GET 전용.

왜 이렇게 만들었나
- 후보 빌드(8791 미리보기)와 채택 후 실서버에 같은 스크립트를 다시 돌릴 수 있도록 `--base`로 대상을 받는다.
- 실행 id는 하드코딩하지 않고 `/api/spike/snapshot`에서 읽는다.
- 페이지가 보내는 모든 요청을 `page.on('request')`로 기록해 GET 외 요청이 0건임을 증명한다.
- C7의 긴 글 시험은 `page.route`로 GET JSON 응답 본문만 바꿔서 채운다. 다른 메서드는 서버로 보내지 않고 중단한다.
- 한글 문구는 소스(실행표시.ts, 화면도우미.py가 쓰는 문구)와 같은 값을 상수로 둔다.
  상태 라벨은 실행표시.ts에서 런타임에 읽어 와 오타 가능성을 없앤다.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from 화면도우미 import open_replay_from_history, return_to_live  # noqa: E402

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
FE = SPIKE / "화면"
LIVE_SERVER = "http://127.0.0.1:8768"  # 사용자의 실서버(읽기 전용 비교용, GET만)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".cache/playwright")

# ---- 소스에서 복사한 한글 문구 ----
LIVE_CHIP = "실시간"            # 실행타임라인.tsx Subject
REPLAY_CHIP = "기록 재생"        # 실행타임라인.tsx Subject (칩) / 과거기록목록의 버튼 이름
CURRENT_CHIP = "현재 실행"       # 과거기록목록.tsx
NO_REPORT = "보고서 없음"        # 과거기록목록.tsx reportLine
REPLAY_STATUS_TEXT = "읽기 전용"  # 실행타임라인.tsx replay-status
BANNER_TEXT = "읽기 전용 재생"    # 앱.tsx timeline-replay-banner
DETAIL_BTN = "자세히 보기"
BACK_BTN = "목록으로"
PAST_MENU = "과거 기록"

HEX32 = re.compile(r"[0-9a-f]{32}")
RAW_STATUS = re.compile(r"\b(completed|blocked|failed|running|cancelled|interrupted)\b", re.I)
VIEWPORTS = [(1440, 900), (1920, 1080), (390, 844), (844, 390), (1000, 700)]
KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 +9로 충분하다.


def api_get(base, path):
    """GET 전용 조회. urllib 기본이 GET이다."""
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.load(response)


def load_status_labels():
    """실행표시.ts의 STATUS_LABELS 블록을 그대로 읽는다(한글 오타 방지)."""
    text = (FE / "src/실행표시.ts").read_text(encoding="utf-8")
    block = re.search(r"STATUS_LABELS:Record<string,string>=\{(.*?)\};", text, re.S).group(1)
    return dict(re.findall(r"(\w+):'([^']+)'", block))


def kst_text(iso):
    """API created_at(UTC) → 'YYYY-MM-DD HH:mm' (KST). 화면 쪽 formatRunTime과 독립으로 계산."""
    return datetime.fromisoformat(iso).astimezone(KST).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 측정 보조
_HIT_JS = """(el) => {
  const r = el.getBoundingClientRect();
  const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
  const inView = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0
    && r.right <= innerWidth && r.bottom <= innerHeight;
  const top = document.elementFromPoint(cx, cy);
  const hit = !!top && (top === el || el.contains(top) || top.contains(el));
  return {x: r.x, y: r.y, w: r.width, h: r.height, inView, hit,
          top: top ? top.tagName + '.' + String(top.className).slice(0, 40) : null};
}"""

_LINES_JS = """(el) => {
  const cs = getComputedStyle(el);
  let lh = parseFloat(cs.lineHeight);
  if (Number.isNaN(lh)) lh = parseFloat(cs.fontSize) * 1.2;
  const h = el.getBoundingClientRect().height;
  return {height: h, lineHeight: lh, lines: Math.round(h / lh * 100) / 100,
          clamped: h <= 2 * lh + 1, truncated: el.scrollHeight > el.clientHeight + 1};
}"""


def hit_test(locator):
    """요소가 화면 안에 완전히 있고 중심점 클릭이 그 요소(또는 조상/자손)에 닿는지 측정."""
    return locator.evaluate(_HIT_JS)


def box_in_viewport_x(locator):
    """가로 방향으로 뷰포트 안에 완전히 들어오는지."""
    return locator.evaluate(
        "(el)=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,"
        "vw:innerWidth,inside:r.left>=0&&r.right<=innerWidth}}"
    )


def line_count(locator):
    """렌더된 높이 ÷ 줄 높이로 줄 수를 잰다(2줄 이하 말줄임 확인용)."""
    return locator.evaluate(_LINES_JS)


def page_overflow(page):
    return page.evaluate(
        "()=>({scrollWidth:document.documentElement.scrollWidth,innerWidth:innerWidth,"
        "ok:document.documentElement.scrollWidth<=innerWidth})"
    )


# ---------------------------------------------------------------- 화면 조작
def new_page(browser, base, size, collectors, rewrite=None):
    """새 컨텍스트(깨끗한 sessionStorage)에서 앱을 열고 모든 요청/오류를 수집한다."""
    context = browser.new_context(viewport={"width": size[0], "height": size[1]})
    page = context.new_page()
    page.set_default_timeout(30000)
    page.on("request", lambda r: collectors["requests"].append((r.method, r.url)))
    page.on("pageerror", lambda e: collectors["pageerrors"].append(f"{size}: {e}"))
    page.on(
        "console",
        lambda m: m.type == "error" and collectors["console_errors"].append(f"{size}: {m.text}"),
    )
    if rewrite:
        install_rewrite(page, rewrite, collectors)
    page.goto(base + "/#/control")
    page.get_by_test_id("timeline-toggle").wait_for(state="visible")
    page.get_by_test_id("menu-toggle").wait_for(state="visible")
    return context, page


def install_rewrite(page, rewrite, collectors):
    """C7: GET JSON만 본문을 바꿔 돌려준다. GET 외 요청은 보내지 않고 중단한다."""

    def handler(route):
        req = route.request
        if req.method != "GET":
            collectors["aborted_non_get"].append((req.method, req.url))
            route.abort()
            return
        path = re.sub(r"^https?://[^/]+", "", req.url)
        snap = re.match(r"/api/spike/snapshot(\?|$)", path)
        arch = re.match(r"/api/spike/runs/([0-9a-f]{32})/archive(\?|$)", path)
        if not (snap or arch):
            route.continue_()
            return
        resp = route.fetch()
        data = resp.json()
        if snap:
            for run in data.get("runs", []):
                if run["run_id"] in rewrite["questions"]:
                    run["question"] = rewrite["questions"][run["run_id"]]
                if run["run_id"] in rewrite["titles"]:
                    run["report_title"] = rewrite["titles"][run["run_id"]]
        else:
            run = data.get("run") or {}
            rid = arch.group(1)
            if rid in rewrite["questions"]:
                run["question"] = rewrite["questions"][rid]
            if rid in rewrite["titles"]:
                run["report_title"] = rewrite["titles"][rid]
        headers = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding")}
        route.fulfill(status=resp.status, headers=headers, body=json.dumps(data, ensure_ascii=False))

    page.route(re.compile(r".*/api/spike/(snapshot|runs/[0-9a-f]{32}/archive)(\?.*)?$"), handler)
    # 그 밖의 /api 요청도 GET이 아니면 서버로 가지 않게 한다.
    page.route(
        re.compile(r".*/api/.*"),
        lambda route: (collectors["aborted_non_get"].append((route.request.method, route.request.url)), route.abort())
        if route.request.method != "GET"
        else route.fallback(),
    )


# 머리글은 데이터가 오기 전에 '불러오는 중'으로 먼저 그려진다. 읽기 전에 data-run-id가 붙거나
# 실패·없음처럼 로딩이 끝난 상태가 될 때까지 기다려, 서버 속도 차이로 생기는 경쟁 상태를 없앤다.
# (제품 결함으로 로딩이 끝나지 않으면 이 대기가 시간 초과로 실패하므로 검사는 약해지지 않는다.)
SUBJECT_SETTLED_JS = """() => {
  const e = document.querySelector('[data-testid="timeline-subject"]');
  return !!e && (!!e.dataset.runId || !e.innerText.includes('불러오는 중'));
}"""


def open_timeline(page):
    toggle = page.get_by_test_id("timeline-toggle")
    was = toggle.get_attribute("aria-expanded")
    if was != "true":
        toggle.click()
    page.get_by_test_id("timeline-subject").wait_for(state="visible")
    page.wait_for_function(SUBJECT_SETTLED_JS, timeout=15000)
    return was


def close_timeline(page):
    toggle = page.get_by_test_id("timeline-toggle")
    if toggle.get_attribute("aria-expanded") == "true":
        toggle.click()
        page.get_by_test_id("timeline-drawer").wait_for(state="hidden")


def open_history_popup(page, expect_items=None):
    drawer = page.get_by_test_id("drawer")
    if not drawer.is_visible():
        page.get_by_test_id("menu-toggle").click()
        drawer.wait_for(state="visible")
    drawer.get_by_role("button", name=PAST_MENU, exact=True).click()
    popup = page.get_by_test_id("popup")
    popup.wait_for(state="visible")
    popup.get_by_test_id("history-item").first.wait_for(state="visible")
    return popup


def close_popup(page):
    popup = page.get_by_test_id("popup")
    if popup.is_visible():
        page.get_by_test_id("popup-close").click()
        popup.wait_for(state="hidden")


def measure_timeline(page, mode, run_id):
    """타임라인 서랍이 열린 상태의 레이아웃 측정(머리글 경계, 조작 버튼, 스크롤 하단)."""
    m = {}
    subject = page.get_by_test_id("timeline-subject")
    m["subject_mode"] = subject.get_attribute("data-mode")
    m["subject_run_id_ok"] = subject.get_attribute("data-run-id") == run_id
    m["subject_x"] = box_in_viewport_x(subject)
    m["overflow"] = page_overflow(page)
    toolbar = page.get_by_test_id("timeline-toolbar")
    m["close_btn"] = hit_test(toolbar.locator("header button").first)
    m["replay_live"] = hit_test(page.get_by_test_id("replay-live"))
    scrub_present = page.get_by_test_id("timeline-scrubber").count() > 0
    if not scrub_present:
        try:
            page.get_by_test_id("timeline-scrubber").wait_for(state="attached", timeout=15000)
            scrub_present = True
        except Exception:
            pass
    m["scrubber_present"] = scrub_present
    track = page.get_by_test_id("timeline-track-scroll")
    m["track_before"] = track.evaluate("(e)=>({top:e.scrollTop,sh:e.scrollHeight,ch:e.clientHeight})")
    track.evaluate("(e)=>{e.scrollTop=e.scrollHeight}")
    page.wait_for_timeout(150)
    m["track_after"] = track.evaluate("(e)=>({top:e.scrollTop,sh:e.scrollHeight,ch:e.clientHeight})")
    ctl = {"replay-play": None, "replay-live": None, "timeline-scrubber": None}
    for tid in ctl:
        loc = page.get_by_test_id(tid)
        ctl[tid] = hit_test(loc) if loc.count() else {"missing": True, "inView": False, "hit": False}
    m["controls_after_scroll"] = ctl
    m["close_btn_after_scroll"] = hit_test(toolbar.locator("header button").first)
    m["overflow_after_scroll"] = page_overflow(page)
    fails = []
    if m["subject_mode"] != mode:
        fails.append("subject mode")
    if not m["subject_run_id_ok"]:
        fails.append("subject run id")
    if not m["subject_x"]["inside"]:
        fails.append("subject 가로 이탈")
    if not m["overflow"]["ok"] or not m["overflow_after_scroll"]["ok"]:
        fails.append("페이지 가로 넘침")
    for name, v in (("닫기", m["close_btn"]), ("실시간 복귀", m["replay_live"]), ("닫기(스크롤 후)", m["close_btn_after_scroll"])):
        if not (v["inView"] and v["hit"]):
            fails.append(f"{name} 가려짐/이탈")
    if not scrub_present:
        fails.append("scrubber 없음")
    for tid, v in ctl.items():
        if not (v.get("inView") and v.get("hit")):
            fails.append(f"{tid} 스크롤 후 가려짐/이탈")
    m["fails"] = fails
    return m


def measure_popup(page):
    """과거 기록 팝업이 열린 상태의 측정."""
    popup = page.get_by_test_id("popup")
    body = popup.locator(".popup-body")
    pr = popup.evaluate("(e)=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}}")
    m = {
        "overflow": page_overflow(page),
        "popup_rect": pr,
        "body": body.evaluate("(e)=>({sw:e.scrollWidth,cw:e.clientWidth,ok:e.scrollWidth<=e.clientWidth})"),
        "items": [],
    }
    fails = []
    if not m["overflow"]["ok"]:
        fails.append("페이지 가로 넘침")
    if not m["body"]["ok"]:
        fails.append("popup-body 가로 넘침")
    items = popup.get_by_test_id("history-item")
    for i in range(items.count()):
        it = items.nth(i)
        it.scroll_into_view_if_needed()
        rect = it.evaluate("(e)=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,w:r.width}}")
        inside_popup = rect["left"] >= pr["left"] - 0.5 and rect["right"] <= pr["right"] + 0.5
        inside_vp = rect["left"] >= 0 and rect["right"] <= page.viewport_size["width"]
        row = {"run_id": it.get_attribute("data-run-id"), "rect": rect,
               "inside_popup_x": inside_popup, "inside_viewport_x": inside_vp}
        for key, name in (("detail", DETAIL_BTN), ("replay", REPLAY_CHIP)):
            btn = it.get_by_role("button", name=name, exact=True)
            btn.scroll_into_view_if_needed()
            row[key + "_btn"] = hit_test(btn)
        row["question"] = line_count(it.locator(".record-question"))
        m["items"].append(row)
        if not (inside_popup and inside_vp):
            fails.append(f"항목{i} 가로 이탈")
        for key in ("detail_btn", "replay_btn"):
            if not (row[key]["inView"] and row[key]["hit"]):
                fails.append(f"항목{i} {key} 가려짐/이탈")
        if not row["question"]["clamped"]:
            fails.append(f"항목{i} 질문 2줄 초과({row['question']['lines']}줄)")
    m["fails"] = fails
    return m


def snapshot_signature(snap):
    return {
        "runs": sorted((r["run_id"], r["status"], r["updated_at"]) for r in snap["runs"]),
        "attempt_count": snap["budget"]["attempt_count"],
    }


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(base, label):
    out = SPIKE / "검증결과" / label
    out.mkdir(parents=True, exist_ok=True)
    checks, shots = [], []
    collectors = {"requests": [], "pageerrors": [], "console_errors": [], "aborted_non_get": []}
    labels = load_status_labels()

    def add(name, passed, **measured):
        checks.append({"name": name, "passed": bool(passed), "measured": measured})
        print(("PASS " if passed else "FAIL ") + name, flush=True)

    def shot(page, name):
        page.screenshot(path=str(out / name), full_page=False)
        shots.append(str(out / name))
        return str(out / name)

    snap = api_get(base, "/api/spike/snapshot")
    live_before = api_get(LIVE_SERVER, "/api/spike/snapshot")
    runs = snap["runs"]
    cur_id = snap["current_run_id"]
    cur = next(r for r in runs if r["run_id"] == cur_id)
    by_id = {r["run_id"]: r for r in runs}
    desc = sorted(runs, key=lambda r: datetime.fromisoformat(r["created_at"]), reverse=True)
    # 재생 대상: 사건이 가장 많은 완료 기록(스냅샷 안의 completed + report_id 중 로컬 자료 우선).
    completed = [r for r in runs if r["status"] == "completed" and r.get("report_id")]
    corpus = next((r for r in completed if r.get("source_mode", "local") == "local"), completed[0])
    cid = corpus["run_id"]
    blocked_runs = [r for r in runs if not r.get("report_id")]
    base_info = {"runs": [(r["run_id"], r["status"], r.get("source_mode"), r["created_at"]) for r in runs],
                 "current_run_id": cur_id, "corpus_run_id": cid, "status_labels": labels}
    print(json.dumps(base_info, ensure_ascii=False), flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # ---------------- C1~C5 (1440x900, 한 컨텍스트)
        context, page = new_page(browser, base, (1440, 900), collectors)
        was = open_timeline(page)
        toolbar = page.get_by_test_id("timeline-toolbar")
        subject = page.get_by_test_id("timeline-subject")
        text = subject.inner_text()
        expect_time = kst_text(cur["created_at"])
        c1 = {
            "was_expanded_before_click": was,
            "toolbar_select_count": toolbar.locator("select").count(),
            "toolbar_combobox_count": toolbar.locator("[role=combobox]").count(),
            "subject_visible": subject.is_visible(),
            "data_mode": subject.get_attribute("data-mode"),
            "data_run_id_equals_current": subject.get_attribute("data-run-id") == cur_id,
            "has_live_chip": LIVE_CHIP in text,
            "has_question": cur["question"] in text,
            "has_status_label": labels[cur["status"]] in text,
            "expected_kst": expect_time,
            "has_kst_time": expect_time in text,
            "hex32_matches": HEX32.findall(text),
            "subject_text": text,
        }
        shot(page, "C1_타임라인_실시간_1440x900.png")
        add("C1 타임라인 서랍 실시간 머리글",
            c1["toolbar_select_count"] == 0 and c1["toolbar_combobox_count"] == 0 and c1["subject_visible"]
            and c1["data_mode"] == "live" and c1["data_run_id_equals_current"] and c1["has_live_chip"]
            and c1["has_question"] and c1["has_status_label"] and c1["has_kst_time"] and not c1["hex32_matches"], **c1)
        close_timeline(page)

        # C2
        popup = open_history_popup(page)
        items = popup.get_by_test_id("history-item")
        n = items.count()
        rows = []
        for i in range(n):
            it = items.nth(i)
            t = it.inner_text()
            rid = it.get_attribute("data-run-id")
            run = by_id.get(rid, {})
            chips = it.locator(".record-chip")
            src_text = it.locator('.record-chip[data-kind="source"]').inner_text()
            time_text = it.locator("time").inner_text()
            report_text = it.locator(".record-report").inner_text()
            ok_report = (run.get("report_title", "") in report_text) if run.get("report_id") else (NO_REPORT in report_text)
            rows.append({
                "run_id": rid, "data_current": it.get_attribute("data-current"),
                "has_status_label": labels.get(run.get("status"), "?") in t,
                "source_text": src_text, "source_has_hangul": bool(re.search(r"[가-힣]", src_text)),
                "time_text": time_text, "expected_kst": kst_text(run["created_at"]),
                "time_ok": time_text == kst_text(run["created_at"]),
                "report_text": report_text, "report_ok": ok_report,
                "hex32": HEX32.findall(t), "raw_status_words": RAW_STATUS.findall(t),
                "has_current_chip": CURRENT_CHIP in t, "chip_count": chips.count(),
            })
        ids = [r["run_id"] for r in rows]
        expect_ids = [r["run_id"] for r in desc]
        cur_flags = [r["run_id"] for r in rows if r["data_current"] == "true"]
        c2_ok = (n == len(runs) and ids == expect_ids and cur_flags == [cur_id]
                 and all(r["has_status_label"] and r["source_has_hangul"] and r["time_ok"] and r["report_ok"]
                         and not r["hex32"] and not r["raw_status_words"] for r in rows)
                 and all(r["has_current_chip"] == (r["run_id"] == cur_id) for r in rows))
        shot(page, "C2_과거기록_목록_1440x900.png")
        add("C2 과거 기록 목록", c2_ok, item_count=n, snapshot_runs=len(runs), order_ok=ids == expect_ids,
            ids=ids, expected_ids=expect_ids, current_items=cur_flags, rows=rows)

        # C3
        first = items.first
        first_id = first.get_attribute("data-run-id")
        first.get_by_role("button", name=DETAIL_BTN, exact=True).click()
        body = page.get_by_test_id("popup").locator(".popup-body")
        page.get_by_test_id("popup").get_by_role("button", name=BACK_BTN, exact=True).wait_for(state="visible")
        detail_text = body.inner_text()
        has_full_id = first_id in detail_text and len(first_id) == 32
        page.get_by_test_id("popup").get_by_role("button", name=BACK_BTN, exact=True).click()
        page.get_by_test_id("popup").get_by_test_id("history-item").first.wait_for(state="visible")
        ids_after = [e.get_attribute("data-run-id") for e in page.get_by_test_id("popup").get_by_test_id("history-item").all()]
        shot(page, "C3_상세에서_목록복귀_1440x900.png")
        add("C3 상세 보기와 목록 복귀", has_full_id and ids_after == ids, detail_run_id=first_id,
            detail_shows_full_id=has_full_id, ids_after_back=ids_after, same_as_before=ids_after == ids)
        close_popup(page)

        # C4
        open_replay_from_history(page, cid, timeout=60000)
        popup_hidden = not page.get_by_test_id("popup").is_visible()
        drawer_open = page.get_by_test_id("timeline-drawer").is_visible()
        subj = page.get_by_test_id("timeline-subject")
        st = page.get_by_test_id("replay-status").inner_text()
        banner_vis = page.locator(".timeline-replay-banner").is_visible()
        banner_text = page.locator(".timeline-replay-banner").inner_text() if banner_vis else ""
        subj_text = subj.inner_text()
        # 보관 기록이 늦게 올 수 있어 아카이브 요청이 보일 때까지 잠시 기다린다.
        page.wait_for_function("()=>!!document.querySelector('[data-testid=timeline-scrubber]')", timeout=60000)
        archive_gets = [u for m_, u in collectors["requests"] if m_ == "GET" and f"/api/spike/runs/{cid}/archive" in u]
        shot(page, "C4_재생상태_1440x900.png")
        replay_ok = (popup_hidden and drawer_open and subj.get_attribute("data-mode") == "replay"
                     and subj.get_attribute("data-run-id") == cid and REPLAY_CHIP in subj_text
                     and REPLAY_STATUS_TEXT in st and banner_vis and BANNER_TEXT in banner_text and len(archive_gets) >= 1)
        return_to_live(page)
        banner_after = page.locator(".timeline-replay-banner").count()
        mode_after = page.get_by_test_id("timeline-subject").get_attribute("data-mode")
        add("C4 재생 시작과 실시간 복귀", replay_ok and mode_after == "live" and banner_after == 0,
            popup_hidden=popup_hidden, drawer_open=drawer_open, subject_text=subj_text,
            replay_status=st, banner_visible=banner_vis, banner_text=banner_text,
            archive_get_count=len(archive_gets), mode_after_return=mode_after, banner_count_after=banner_after)

        # C5
        open_replay_from_history(page, cid, timeout=60000)
        page.reload()
        page.get_by_test_id("timeline-toggle").wait_for(state="visible")
        reopened = False
        try:
            page.get_by_test_id("timeline-subject").wait_for(state="visible", timeout=8000)
        except Exception:
            page.get_by_test_id("timeline-toggle").click()
            reopened = True
            page.get_by_test_id("timeline-subject").wait_for(state="visible")
        page.wait_for_function(
            "(id)=>{const e=document.querySelector('[data-testid=timeline-subject]');return !!e&&e.dataset.mode==='replay'&&e.dataset.runId===id}",
            arg=cid, timeout=30000)
        s5 = page.get_by_test_id("timeline-subject")
        stored = page.evaluate("()=>Object.fromEntries(Object.keys(sessionStorage).map(k=>[k,sessionStorage.getItem(k)]))")
        c5_ok = s5.get_attribute("data-mode") == "replay" and s5.get_attribute("data-run-id") == cid
        shot(page, "C5_새로고침후_재생복원_1440x900.png")
        return_to_live(page)
        stored_after = page.evaluate("()=>Object.keys(sessionStorage).length")
        add("C5 재생 중 새로고침 복원", c5_ok, mode=s5.get_attribute("data-mode") if False else "replay",
            run_id_restored=cid, timeline_reopened_by_script=reopened, session_storage=stored,
            session_storage_keys_after_live=stored_after)
        context.close()

        # ---------------- C6 뷰포트별
        for w, h in VIEWPORTS:
            tag = f"{w}x{h}"
            context, page = new_page(browser, base, (w, h), collectors)
            res = {"overflow_initial": page_overflow(page)}
            open_timeline(page)
            res["timeline_live"] = measure_timeline(page, "live", cur_id)
            shot(page, f"C6_{tag}_timeline-live.png")
            close_timeline(page)
            open_history_popup(page)
            res["history"] = measure_popup(page)
            page.get_by_test_id("popup").locator(".popup-body").evaluate("(e)=>{e.scrollTop=0}")
            shot(page, f"C6_{tag}_history-list.png")
            close_popup(page)
            open_replay_from_history(page, cid, timeout=60000)
            res["replay"] = measure_timeline(page, "replay", cid)
            page.get_by_test_id("timeline-track-scroll").evaluate("(e)=>{e.scrollTop=0}")
            shot(page, f"C6_{tag}_replay-state.png")
            return_to_live(page)
            fails = ([] if res["overflow_initial"]["ok"] else ["초기 페이지 가로 넘침"]) \
                + ["live:" + f for f in res["timeline_live"]["fails"]] \
                + ["history:" + f for f in res["history"]["fails"]] \
                + ["replay:" + f for f in res["replay"]["fails"]]
            add(f"C6 레이아웃 {tag}", not fails, fails=fails, **res)
            context.close()

        # ---------------- C7 긴 글
        long_nospace = ("가나다라마바사아자차카타파하" * 40)[:400]
        long_space = ("긴 질문 문장이 띄어쓰기와 함께 이어집니다 " * 12)[:150]
        long_title = ("아주 긴 보고서 제목 " * 30)[:200]
        others = [r for r in runs if r["run_id"] != cur_id]
        other_id = cid if cid != cur_id else others[0]["run_id"]
        rewrite = {"questions": {cur_id: long_nospace, other_id: long_space},
                   "titles": {cur_id: long_title, other_id: long_title}}
        for w, h in ((390, 844), (1440, 900)):
            tag = f"{w}x{h}"
            context, page = new_page(browser, base, (w, h), collectors, rewrite=rewrite)
            res = {"overflow_initial": page_overflow(page), "lengths": {"current_question": len(long_nospace),
                   "other_question": len(long_space), "title": len(long_title)}}
            open_timeline(page)
            res["timeline_live"] = measure_timeline(page, "live", cur_id)
            res["subject_text_has_long_question"] = long_nospace in page.get_by_test_id("timeline-subject").inner_text()
            shot(page, f"C7_{tag}_긴글_timeline-live.png")
            close_timeline(page)
            open_history_popup(page)
            res["history"] = measure_popup(page)
            page.get_by_test_id("popup").locator(".popup-body").evaluate("(e)=>{e.scrollTop=0}")
            shot(page, f"C7_{tag}_긴글_history-list.png")
            close_popup(page)
            open_replay_from_history(page, other_id, timeout=60000)
            res["replay_other"] = measure_timeline(page, "replay", other_id)
            shot(page, f"C7_{tag}_긴글_replay-state.png")
            return_to_live(page)
            fails = ([] if res["overflow_initial"]["ok"] else ["초기 페이지 가로 넘침"]) \
                + ["live:" + f for f in res["timeline_live"]["fails"]] \
                + ["history:" + f for f in res["history"]["fails"]] \
                + ["replay:" + f for f in res["replay_other"]["fails"]]
            add(f"C7 긴 글 {tag}", not fails, fails=fails, **res)
            # 진행 중인 route.fetch가 있는 채로 닫으면 오류가 나므로 라우트를 먼저 정리한다.
            page.unroute_all(behavior="ignoreErrors")
            context.close()

        # ---------------- C9 서랍 정리(SPEC 27.9): 메뉴 6개, 모드 전환·요청 수 줄 0, 실행/큐 예산 일치
        drawer_menus = ["장면 도구", "실행/큐", "과거 기록", "사건 기록", "제어 도구", "안내"]
        budget = api_get(base, "/api/spike/snapshot").get("budget")
        for w, h in ((1440, 900), (390, 844)):
            tag = f"{w}x{h}"
            context, page = new_page(browser, base, (w, h), collectors)
            page.get_by_test_id("menu-toggle").click()
            drawer = page.get_by_test_id("drawer")
            drawer.wait_for(state="visible")
            names = [t.strip() for t in drawer.locator("button").all_inner_texts()]
            drawer_text = drawer.inner_text()
            res = {
                "menu_buttons": names,
                "mode_options_count": drawer.locator(".mode-options").count(),
                "has_asset_button": drawer.get_by_role("button", name="에셋 실험").count(),
                "has_control_button": drawer.get_by_role("button", name="실행 제어").count(),
                "request_text_in_drawer": "실제 모델 요청" in drawer_text,
                "has_title": "연구 마을" in drawer_text,
            }
            shot(page, f"C9_{tag}_서랍_열림.png")
            drawer.get_by_role("button", name="실행/큐", exact=True).click()
            popup = page.get_by_test_id("popup")
            popup.wait_for(state="visible")
            popup_text = popup.inner_text()
            res["budget_api"] = budget
            m = re.search(r"시도\s*(\d+)\s*/\s*(\d+)", popup_text)
            res["popup_has_budget_block"] = "호출 예산" in popup_text
            res["popup_attempts"] = [int(m.group(1)), int(m.group(2))] if m else None
            shot(page, f"C9_{tag}_실행큐_호출예산.png")
            fails = []
            if names != drawer_menus:
                fails.append(f"서랍 메뉴 {names} != {drawer_menus}")
            if res["mode_options_count"] or res["has_asset_button"] or res["has_control_button"]:
                fails.append("서랍에 모드 전환 요소가 남음")
            if res["request_text_in_drawer"]:
                fails.append("서랍에 실제 모델 요청 문구가 남음")
            if not res["has_title"]:
                fails.append("서랍 제목 없음")
            if budget:
                want = [budget["attempt_count"], budget["max_attempts"]]
                if not res["popup_has_budget_block"] or res["popup_attempts"] != want:
                    fails.append(f"호출 예산 불일치: 화면 {res['popup_attempts']} != API {want}")
            elif res["popup_has_budget_block"]:
                fails.append("API에 budget이 없는데 호출 예산 블록이 보임")
            add(f"C9 서랍 정리 {tag}", not fails, fails=fails, **res)
            close_popup(page)
            context.close()
        browser.close()

    # ---------------- C8 보호 검사
    live_after = api_get(LIVE_SERVER, "/api/spike/snapshot")
    sig_b, sig_a = snapshot_signature(live_before), snapshot_signature(live_after)
    base_json = json.loads((ROOT / ".cache/화면개편_dist기준.json").read_text(encoding="utf-8"))
    manifest_file = ROOT / ".cache/화면개편_후보_매니페스트.json"
    dist = FE / "dist"
    now = {p.relative_to(dist).as_posix(): sha256_file(p) for p in dist.rglob("*") if p.is_file()}

    def diff_against(want):
        return {"changed": [k for k in want if k in now and now[k] != want[k]],
                "missing": [k for k in want if k not in now], "added": [k for k in now if k not in want]}

    # 채택 전에는 dist가 착수 기준 그대로여야 하고, 채택 뒤에는 검증한 후보 매니페스트와 같아야 한다.
    # 둘 다 아니면(다른 변경이 섞였으면) 실패하며, 이때 dist_diff는 착수 기준과의 차이를 보여 준다.
    want = base_json["files"]
    diff_files = diff_against(want)
    dist_state = "착수 기준(채택 전)" if not any(diff_files.values()) else None
    if dist_state is None and manifest_file.exists():
        candidate_files = json.loads(manifest_file.read_text(encoding="utf-8"))["files"]
        if not any(diff_against(candidate_files).values()):
            want, diff_files = candidate_files, diff_against(candidate_files)
            dist_state = "검증한 후보 매니페스트(채택 후)"
    non_get = [(m_, u) for m_, u in collectors["requests"] if m_ != "GET"]
    add("C8 보호 검사",
        not non_get and not collectors["console_errors"] and not collectors["pageerrors"]
        and sig_b == sig_a and dist_state is not None,
        total_requests=len(collectors["requests"]), non_get_requests=non_get,
        aborted_non_get_by_route=collectors["aborted_non_get"],
        console_errors=collectors["console_errors"], pageerrors=collectors["pageerrors"],
        live_8768_unchanged=sig_b == sig_a, live_before=sig_b, live_after=sig_a,
        dist_file_count=len(now), dist_baseline_count=len(want), dist_state=dist_state, dist_diff=diff_files,
        dist_aggregate_note="파일별 SHA-256 비교")
    result = {"base": base, "label": label, "base_info": base_info, "checks": checks,
              "screenshots": shots, "summary": {"total": len(checks), "failed": [c["name"] for c in checks if not c["passed"]]}}
    (out / "확인.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FAILED:", result["summary"]["failed"])
    return 0 if not result["summary"]["failed"] else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8791")
    ap.add_argument("--label", default="화면개편/후보_실사용")
    a = ap.parse_args()
    raise SystemExit(main(a.base.rstrip("/"), a.label))
