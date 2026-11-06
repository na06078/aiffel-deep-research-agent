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
---

# Design System: 딥리서치 에이전트 · 전체 화면 마을 스파이크

## Overview

**Creative North Star: "마을이 작업 화면"**

픽셀 마을이 브라우저 작업 영역 전체이며 상세·큐·기록은 필요할 때 그 위에서 엽니다. 작은 지도를 대시보드 안에 넣던 이전 배치를 폐기했습니다. 현재 구현의 기록이지 정식 앱 전체나 새 브랜드를 승인받았다는 증거가 아닙니다.

**Key Characteristics:**
- 전체 viewport의 마을과 실제 역할의 NPC·작업대·제출 문서.
- 작은 햄버거·모의 응답 표시·하단 입력만 기본 노출.
- 같은 세계 위의 한 팝업과 보존되는 입력·고정 사건 선택.

근거는 새 `화면/src/화면.css`, `마을.css`, `앱.tsx`, `마을.tsx`, `상세보기.tsx`와 `검증결과/최종_확인/`입니다. 이전 2단 화면과 ship 판정은 새 화면 수용 근거가 아닙니다.

## Colors

### Primary
짙은 녹색 primary는 전송·링크·캐럿에 씁니다. hover를 구별합니다.

### Neutral
종이색 paper와 짙은 ink는 팝업·입력·메뉴의 읽기 영역입니다. line/frame으로 경계를, code-background로 원문을 구별합니다. grass는 장면 바탕이며 빈 업무 패널이 아닙니다. 말풍선은 따뜻한 종이색과 갈색 경계입니다.

## Typography

외부 웹폰트 없이 한국어 시스템 글꼴을 사용합니다. 큰 페이지 제목을 마을 위에 추가하지 않습니다. 원문은 Consolas 계열과 줄바꿈을 사용합니다. NPC 이름은 13px/좁은 화면11px, 말풍선 안내는 10px/9px와 opacity .72입니다.

## Layout

world/canvas는 (0,0,innerWidth,innerHeight)입니다. 팝업·모드·사건 갱신은 세계를 축소하거나 canvas를 재생성하지 않습니다. 페이지 자체의 가로·세로 스크롤은 없습니다.

햄버거는 위/왼쪽16px, 44×44px입니다. 중앙 창은 최대880px·위72px이며 본문만 스크롤합니다. 하단 입력은 최대760px·아래16px입니다. 폭600px 이하 및 높이520px 이하 배치가 따로 있습니다. 마을은 정수2/3/4 배율과 가로/세로 작업장 배치로 viewport를 채웁니다. 고정8:5 지도와 상시 오른쪽 칼럼을 되살리지 않습니다.

## Elevation & Depth

팝업은 0 14px 44px #233e2c45, 입력은 0 5px 22px #233e2c45, 말풍선은 0 5px 14px #344b252b의 부드러운 그림자입니다. 장식용 유리 효과나 세계 전체를 덮는 고정 업무 영역은 없습니다.

## Shapes

버튼·창·말풍선 반경은 frontmatter를 따릅니다. Kenney 원본 픽셀과 투명도를 보존하며 nearest·antialias false·roundPixels·CSS pixelated를 사용합니다. 집은 지붕과 벽/문을 조립하고 나무는 실제2×2 타일을 사용합니다.

## Components

- **Buttons:** 최소42px, 아이콘44×44px. hover/active/disabled와3px focus outline을 구별하며 SVG 아이콘을 사용합니다.
- **Textarea:** 작은 두 줄 고정 입력. 실패·조회·모드 전환에도 미제출 문구를 보존하며 팝업에 가리지 않습니다.
- **Navigation:** 햄버거 안에서 모드와 여섯 업무 메뉴를 선택합니다. 과거 기록의 목록/상세/복귀는 같은 창입니다.
- **Speech bubble:** 실제 질문·배정 제목·관측 상태를 쓰며 내부 사건명만으로 업무 대화를 대신하지 않습니다. 전체를 누를 수 있고 오른쪽 아래에 (클릭해서 더 자세히)를 둡니다. 코드 오브젝트 본문은 ... 입니다. 상세는 선택 ID를 유지합니다.
- **Report:** 제출 문서를 눌러 같은 확정 Markdown을 읽고 다운로드합니다. 실패 초안이나 다른 실행으로 바꾸지 않습니다.
- **Motion:** 정면 포즈를 코드로 이동1500ms·작업1600ms·운반2200ms합니다. 새 큐를 연출로 늦추지 않습니다. reduced-motion은 이동·바운스를 줄입니다. 방향별 걷기 프레임은 아닙니다.

## Do's and Don'ts

### Do:
- **Do** 전체 마을·작은 하단 입력·필요할 때 여는 창의 관계를 유지합니다.
- **Do** 고정 사건/보고서 ID와 실제 입출력·KST/원본 UTC를 구별합니다.
- **Do** 모의 응답과 실제 모델 미호출을 명확히 표시합니다.

### Don't:
- **Don't** 상시 오른쪽 칼럼·표·로그로 마을을 둘러싸거나 축소하지 않습니다.
- **Don't** 관측하지 않은 진행률·가짜 대화를 실제 연구로 표시하지 않습니다.
- **Don't** 이 스파이크를 정식 앱 전체·실제 GPT 연구·HTML/PDF 완료로 확대하지 않습니다.
