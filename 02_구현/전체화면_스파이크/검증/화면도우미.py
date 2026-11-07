"""검증 스크립트가 함께 쓰는 Playwright 화면 도우미.

타임라인의 실행 선택 목록(select)이 없어진 뒤, 과거 실행의 읽기 전용 재생은
햄버거 메뉴 → `과거 기록` 팝업 → 항목의 `기록 재생` 버튼으로만 시작한다.
이 모듈은 그 경로와 실시간 복귀를 한곳에 모아 검증 스크립트가 같은 방식으로 쓰게 한다.

이 파일은 브라우저를 직접 열지 않는다. 호출하는 쪽이 만든 Playwright `page`만 받는다.
"""

import json

# 재생 대상이 타임라인 머리글에 반영됐는지 브라우저 안에서 확인하는 식.
_REPLAY_SUBJECT_JS = """(runId) => {
  const el = document.querySelector('[data-testid="timeline-subject"]');
  return !!el && el.dataset.mode === 'replay' && el.dataset.runId === runId;
}"""


def open_replay_from_history(page, run_id, timeout=10000):
    """`과거 기록` 팝업에서 `run_id` 실행의 `기록 재생`을 눌러 읽기 전용 재생을 시작한다.

    순서: 메뉴 서랍이 닫혀 있을 때만 햄버거를 눌러 연다 → `과거 기록` 메뉴 →
    `data-run-id`가 일치하는 항목이 보일 때까지 기다림 → 그 항목의 `기록 재생` 클릭 →
    팝업이 닫히고 타임라인 머리글이 `data-mode=replay`, `data-run-id=run_id`가 될 때까지 기다림.
    재생을 시작하면 앱이 타임라인 서랍을 자동으로 연다.
    """
    drawer = page.get_by_test_id("drawer")
    if not drawer.is_visible():
        page.get_by_test_id("menu-toggle").click()
        drawer.wait_for(state="visible", timeout=timeout)
    drawer.get_by_role("button", name="과거 기록", exact=True).click()
    popup = page.get_by_test_id("popup")
    item = popup.locator(f'[data-testid="history-item"][data-run-id={json.dumps(run_id)}]')
    item.wait_for(state="visible", timeout=timeout)
    item.get_by_role("button", name="기록 재생", exact=True).click()
    popup.wait_for(state="hidden", timeout=timeout)
    page.wait_for_function(_REPLAY_SUBJECT_JS, arg=run_id, timeout=timeout)


def return_to_live(page, timeout=10000):
    """`실시간 복귀`(`replay-live`)를 눌러 타임라인 머리글이 `data-mode=live`가 될 때까지 기다린다."""
    page.get_by_test_id("replay-live").click()
    page.wait_for_function(
        """() => {
          const el = document.querySelector('[data-testid="timeline-subject"]');
          return !!el && el.dataset.mode === 'live';
        }""",
        timeout=timeout,
    )
