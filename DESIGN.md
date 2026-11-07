---
name: 딥리서치 에이전트 · 전체 화면 마을 스파이크
description: 전체 화면의 픽셀 마을과 필요할 때 여는 업무 창
colors:
  primary: "#285a3c"
  primary-hover: "#214d32"
  paper: "#fffdf4"
  ink: "#253c2c"
  line: "#c0cbb6"
  frame: "#7f946e"
  grass: "#94b84a"
  code-background: "#edf1e5"
  bubble: "#fff5dcef"
  bubble-ink: "#302b20"
  bubble-border: "#78664c"
  timing-node: "#b6d7c4"
  timing-wait: "#f2cf8d"
  timing-surface: "#fcfaef"
typography:
  body:
    fontFamily: '"Segoe UI","Malgun Gothic",system-ui,sans-serif'
    fontSize: "14px"
    lineHeight: 1.7
  title:
    fontSize: "19px"
    fontWeight: 700
    lineHeight: 1.4
  label:
    fontSize: "11px"
  code:
    fontFamily: "Consolas,monospace"
    fontSize: "14px"
    lineHeight: 1.7
rounded:
  button: "6px"
  window: "9px"
  bubble: "8px"
spacing:
  compact: "8px"
  group: "12px"
  outer: "16px"
  header: "20px"
  body: "26px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.paper}"
    rounded: "{rounded.button}"
    padding: "10px 14px"
  textarea:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    padding: "7px 10px"
    height: "56px"
  speech-bubble:
    backgroundColor: "{colors.bubble}"
    textColor: "{colors.bubble-ink}"
    rounded: "{rounded.bubble}"
    padding: "10px 12px"
  popup:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.window}"
    width: "880px"
  timing-drawer:
    backgroundColor: "{colors.timing-surface}"
    textColor: "{colors.ink}"
    padding: "12px 16px max(12px,env(safe-area-inset-bottom))"
---

# Design System: 딥리서치 에이전트 · 전체 화면 마을 스파이크

## Overview

**Creative North Star: "마을이 작업 화면"**

픽셀 마을이 브라우저 작업 영역 전체이며 상세·큐·기록은 필요할 때 그 위에서 엽니다. 규칙적인 여섯 업무 거점 배치도에서 벗어나 주거 골목·마당·장터 광장·숲/물가가 함께 있는 생활 공간을 구성합니다. 입력 아래 손잡이로 객체별 시간창을 열고 저장된 사건을 읽기 전용으로 복기합니다. 현재 구현 기록이지 정식 앱 전체나 사용자 최종 취향 승인의 증거는 아닙니다.

**Key Characteristics:**
- 전체 viewport의 마을과 실제 역할의 NPC·작업대·제출 문서.
- 작은 햄버거·하단 입력·그 아래 얇은 시간창 손잡이만 기본 노출. 시험/에셋 모드에서만 모의 응답을 표시하고 실제 모드에 고정 대역 문구를 붙이지 않는다.
- 같은 세계 위의 한 팝업과 보존되는 입력·고정 사건 선택.

근거는 `화면/src/화면.css`, `마을.css`, `실행타임라인.css`, `마을배치.ts`, `마을지도.ts`, `마을풍경.ts`, `시간재생.ts`입니다. 2026-11-07 마을 재설계 후보의 현재 근거는 `검증결과/마을재설계/메인_최종확인.json`·`실사용_최종/확인.json`과 실제 캡처입니다. 이전 `시간_최종검사/`·`마을_최종장면/`·`시간_시연_확인.json`은 당시 이력입니다. 이전 2단 화면이나 거점 여섯 개 지도·ship 판정은 새 디자인의 수용 근거가 아닙니다. 후보는 별도8790이며 기존8768에 적용하지 않았고 사용자 취향 채택도 아직입니다.

## Colors

### Primary
짙은 녹색 primary는 전송·링크·캐럿에 씁니다. hover를 구별합니다.

### Neutral
종이색 paper와 짙은 ink는 팝업·입력·메뉴의 읽기 영역입니다. line/frame으로 경계를, code-background로 원문을 구별합니다. grass는 장면 바탕이며 빈 업무 패널이 아닙니다. 말풍선은 따뜻한 종이색과 갈색 경계입니다.

## Typography

외부 웹폰트 없이 한국어 시스템 글꼴을 사용합니다. 큰 페이지 제목을 마을 위에 추가하지 않습니다. 원문은 Consolas 계열과 줄바꿈을 사용합니다. NPC 이름은 13px/좁은 화면11px, 말풍선 안내는 10px/9px와 opacity .72입니다.

## Layout

world/canvas는 (0,0,innerWidth,innerHeight)입니다. 팝업·모드·사건 갱신은 세계를 축소하거나 canvas를 재생성하지 않습니다. 페이지 자체의 가로·세로 스크롤은 없습니다.

햄버거는 위/왼쪽16px, 44×44px입니다. 중앙 창은 최대880px·기본 위70px이며 본문만 스크롤합니다. 하단 입력은 최대760px이며 시간창 높이와 함께 위치를 조정합니다. 입력/알림/재생 배너의 실제 위치를 ResizeObserver로 관측하여 팝업과12px 간격을 확보하고, 알림이 여러 줄이어도 본문 끝을 가리지 않습니다. 마을과 업무 표면은 좁은/낮은 창의 배치를 따로 가집니다. 마을은 정수 배율과 화면에 맞는 장소 배치로 viewport를 채웁니다. 고정8:5 지도와 상시 오른쪽 칼럼을 되살리지 않습니다.

시간창은 초기 `min(40dvh,360px)`에서 시작하고 상단 경계 드래그/키보드로 높이를 조절합니다. 실제 입력/메뉴/팝업의 공간을 남긴 유효 높이를 한 CSS 변수로 입력·손잡이·드로어에 공유합니다. 사용자가 고른 높이는 접기/재열기에서 유지하고 임시 viewport/팝업 제한과 구별합니다. 실행 선택·재생·시점/시간눈금은 고정, 행만 스크롤하며 같은 시점과 행의 위치를 잃지 않습니다. 시간창·시점이 바뀌어도 같은 canvas와 세계 경계를 유지합니다. 마을의 여섯 업무 장소 외에 넓은 화면 주거집6채·좁은/가로형4채와 열린 장터·마당을 배치합니다. 배경 집은 추가 AI 에이전트가 아닙니다.

## Elevation & Depth

팝업은 0 14px 44px #233e2c45, 입력은 0 5px 22px #233e2c45, 말풍선은 0 5px 14px #344b252b의 부드러운 그림자입니다. 장식용 유리 효과나 세계 전체를 덮는 고정 업무 영역은 없습니다.

## Shapes

버튼·창·말풍선 반경은 frontmatter를 따릅니다. Kenney 원본 픽셀과 투명도를 보존하며 nearest·antialias false·roundPixels·CSS pixelated를 사용합니다. 원본16px atlas에서 집은 지붕2행·막힌 벽·개별 문과 창을 구별해 조립합니다. 나무는 **1열×2행**(초록4열/주황3열의0·1행)을 한 실루엣으로 사용하고 실제 발밑 y로 정렬합니다. 과거의2×2 조립 설명은 잘못된 관찰이었으며 인접한 수관을 합친 구현과 함께 교체했습니다.

### 마을 재설계 후보의 지면과 배치

- 같은 순수 지도에서 길·주거집·숲·물가를 읽습니다. 주 통행로와 광장에 각 업무 장소·주거 골목이 연결되고 이동/운반도 같은 길의 점 배열을 사용합니다. 포장을 정수 픽셀 합집합으로 먼저 묶은 뒤 바깥 경계만 그려 교차부 안에 이중 테두리를 남기지 않습니다.
- 주거 골목은7논리 픽셀의 보행 폭이며 문턱만 좁게 접속합니다. 실제 캡처에서 가느다란 기둥처럼 보이던 긴 진입로의 가독성을 한 차례 보완했습니다. 원본 셀과 정수 세계 배율2를 사용하고 넓음/좁음/낮음 배치를 구별합니다.
- 숲 무리는 저작한 중심과 간격으로 구성하며 나무 전체 실루엣을 길/다리/건물/업무/제출 지점에서 비웁니다. 작은 집 두 무리(넓은 화면6채, 좁거나 낮은 화면4채)와 가판대2개의 광장·열린 초지를 구별합니다. 강의 지형 경계와 실제 물 폭으로 다리를 맞추고 양쪽 착지부를 지도 안에 남깁니다.
- 후보의 실제1440×900·1920×1080·390×844·844×390·1000×700 경로를 검사했고 넓은/좁은 장면을 시각 확인했습니다. 낮은 가로 화면의 기존 말풍선/오버레이 가림은 이번 지도 수정과 별도인 기존 UI 한계이며 기능 삭제나 임의 숨김으로 덮지 않았습니다. 시각 검사와 사용자 최종 취향 승인·운영 적용을 구별합니다.

## Components

- **Buttons:** 최소42px, 아이콘44×44px. hover/active/disabled와3px focus outline을 구별하며 SVG 아이콘을 사용합니다.
- **Textarea:** 작은 두 줄 고정 입력. 실패·조회·모드 전환에도 미제출 문구를 보존하며 팝업에 가리지 않습니다.
- **Navigation:** 햄버거 안에서 모드와 여섯 업무 메뉴를 선택합니다. 과거 기록의 목록/상세/복귀는 같은 창입니다.
- **Speech bubble:** 실제 질문·배정 제목·관측 상태를 쓰며 내부 사건명만으로 업무 대화를 대신하지 않습니다. 전체를 누를 수 있고 오른쪽 아래에 (클릭해서 더 자세히)를 둡니다. 코드 오브젝트 본문은 ... 입니다. 상세는 선택 ID를 유지합니다.
- **Report:** 제출 문서를 눌러 같은 확정 Markdown을 읽고 다운로드합니다. 같은 실행의 자동 검사 경고를 함께 표시하고 형식/인용 무결성 통과와 내용 정확성을 구별합니다. 실패 초안이나 다른 실행으로 바꾸지 않습니다.
- **Report exports:** 같은 보고서 팝업의 독립 영역에서 HTML/PDF 미요청·생성 중·준비·실패/재시도를 구별합니다. 기존 색/버튼/포커스 규칙을 재사용하고 좁은 창은 형식별 설명과 조작을 줄바꿈합니다. 정본·연구를 다시 쓰지 않으며 ID+해시와 선택 수명을 고정합니다. 재생 시점에 없는 보고서는 창과 늦은 조회도 해제합니다. 1440×900·390×844·844×390의 실제 캡처·조작/닫기/입력/같은 세계 검사를 통과했습니다.
- **Motion:** 정면 포즈를 코드로 이동1500ms·작업1600ms·운반2200ms합니다. 새 큐를 연출로 늦추지 않습니다. reduced-motion은 이동·바운스를 줄입니다. 방향별 걷기 프레임은 아닙니다.
- **Timing drawer:** 고정 toolbar/시점/눈금 아래에 행 전용 스크롤을 둡니다. 상단10px 경계의 ns-resize·터치·포커스 가능한 separator와 키보드로 높이를 조절합니다. 객체별 행·시각·한국어 단계·녹색 전체/노랑 점선 대기/별도 운반 연출을 구분합니다. 진행 관측과 완료 측정, null 미측정을 구별하고 겹친 시간을 합산하지 않습니다. 짧은 막대의 클릭 최소 폭과 실제ms는 별개입니다. 마지막 행에서도 시점 조작은 위로 스크롤하지 않고 사용합니다.
- **Recorded replay:** 저장 시각을 옮기면 같은 시점은 순서와 관계없이 같은 위치/문서를 재구성합니다. 재생 중 전송은 `기록 재생 중`으로 잠기고 입력은 보존됩니다. 실제 작업을 되돌리거나 다시 호출하지 않습니다.

## Do's and Don'ts

### Do:
- **Do** 전체 마을·작은 하단 입력·필요할 때 여는 창의 관계를 유지합니다.
- **Do** 고정 사건/보고서 ID와 실제 입출력·KST/원본 UTC를 구별합니다.
- **Do** 모의 응답과 실제 제공자 기록을 구별하고 실행/큐 팝업에서 실제 시도/사용량을 사실대로 표시합니다.

### Don't:
- **Don't** 상시 오른쪽 칼럼·표·로그로 마을을 둘러싸거나 축소하지 않습니다.
- **Don't** 관측하지 않은 진행률·가짜 대화를 실제 연구로 표시하지 않습니다.
- **Don't** 기본 실제 연구1건의 실행 성공을 내용 완전 정확성·정식 앱 전체·HTML/PDF 완료로 확대하지 않습니다.
