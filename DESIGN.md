---
name: 딥리서치 에이전트 · 두 스파이크
description: 구현된 작은 픽셀 마을과 실행 제어 UI의 잠정 스타일 기록
colors:
  green: "#286442"
  green-hover: "#204f34"
  ink: "#26382e"
  background: "#f5f5ef"
  surface: "#fffef9"
  line: "#d7ddcf"
  muted: "#576353"
  button-border: "#b4bfac"
  button-hover: "#eaf0e3"
  button-hover-border: "#6d8665"
  button-active: "#dce8d4"
  disabled-ink: "#687363"
  disabled-background: "#eeefe8"
  disabled-border: "#d9ddd2"
  submit-disabled: "#dfe7d9"
  submit-disabled-border: "#c8d1c0"
  submit-disabled-ink: "#59694f"
  focus: "#367546"
  nav-ink: "#53614c"
  nav-current-ink: "#245b3b"
  nav-current: "#e3ebdc"
  selected-event: "#e3eddb"
  input-border: "#b6c1ae"
  placeholder: "#61705a"
  code-background: "#f1f3eb"
  bubble-background: "#fffef3f5"
  bubble-ink: "#263b2a"
  bubble-border: "#657e50"
  error: "#8a3528"
  pause: "#77552a"
typography:
  body:
    fontFamily: '"Segoe UI","Malgun Gothic",system-ui,sans-serif'
    fontSize: "14px"
    lineHeight: 1.6
  headline:
    fontSize: "19px"
    fontWeight: 700
    letterSpacing: "-.5px"
  title:
    fontSize: "16px"
    fontWeight: 700
    lineHeight: 1.4
  label:
    fontSize: "14px"
    fontWeight: 650
  input:
    fontSize: "13px"
    lineHeight: 1.6
  code:
    fontFamily: "Consolas,monospace"
    fontSize: "11px"
    lineHeight: 1.7
rounded:
  label: "3px"
  input: "4px"
  panel: "5px"
  bubble: "7px"
spacing:
  compact: "8px"
  control-gap: "12px"
  narrow-gap: "16px"
  panel-inset: "20px"
  workspace-gap: "24px"
  outer: "28px"
components:
  button-primary:
    backgroundColor: "{colors.green}"
    textColor: "{colors.surface}"
    rounded: "{rounded.panel}"
    padding: "12px 22px"
  button-primary-hover:
    backgroundColor: "{colors.green-hover}"
  button-primary-disabled:
    backgroundColor: "{colors.submit-disabled}"
    textColor: "{colors.submit-disabled-ink}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "7px 13px"
  button-secondary-hover:
    backgroundColor: "{colors.button-hover}"
  button-secondary-active:
    backgroundColor: "{colors.button-active}"
  button-secondary-disabled:
    backgroundColor: "{colors.disabled-background}"
    textColor: "{colors.disabled-ink}"
  textarea:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.input}"
    rounded: "{rounded.input}"
    padding: "11px 12px"
  mode-nav:
    textColor: "{colors.nav-ink}"
    rounded: "{rounded.input}"
    padding: "5px 12px"
  mode-nav-current:
    backgroundColor: "{colors.nav-current}"
    textColor: "{colors.nav-current-ink}"
  speech-bubble:
    backgroundColor: "{colors.bubble-background}"
    textColor: "{colors.bubble-ink}"
    rounded: "{rounded.bubble}"
    padding: "10px 12px 7px"
---

# Design System: 딥리서치 에이전트 · 두 스파이크

## Overview

**Creative North Star: "작은 픽셀 마을의 작업 도구"**

에셋 실험과 실행 제어, 두 프로토타입의 구현값을 추출한 잠정 기록입니다. 밝은 중성 작업 바탕에 짙은 글자와 녹색 조작색을 쓰고, Kenney 픽셀 마을 옆에서 선택한 사건의 입출력을 읽습니다. 새 브랜드 승인이나 정식 앱·실제 GPT 연구 UI의 완성을 뜻하지 않습니다.

**Key Characteristics:**
- 작은 픽셀 장면과 읽기 쉬운 시스템 한국어 UI.
- 선택 사건의 불변 상세와 실제 하단 지시 입력.
- 합성 장면·서버 테스트 대역·실제 모델을 구분하는 고지.

근거: `02_구현/스파이크/화면/src/화면.css`, `앱.tsx`, `마을.tsx`, `상세보기.tsx`, 화면 `index.html`, `public/에셋/에셋_목록.json`. 의도는 `PRODUCT.md`와 `.impeccable/surfaces/02-src-tsx.md`, 기존 실행·캡처 근거는 `02_구현/스파이크/검증결과/최종검사/통합_실제실행.json` 및 같은 폴더의 여섯 PNG입니다. 수치는 원본 CSS 기준이며 캡처에서 역산하지 않았습니다.

## Colors

### Primary
녹색 `green`은 링크·캐럿·지시 전송에 사용합니다. 전송 hover는 더 짙고 일반 버튼 hover는 옅습니다. 포커스는 별도 `focus` 색으로 구별합니다.

### Neutral
`background` 위에 `surface` 패널과 `line` 경계, `ink` 본문, `muted` 보조문을 둡니다. 선택 탐색과 사건 행은 각각 `nav-current`, `selected-event`로 표시합니다. `error`는 입력 전송 오류, `pause`는 큐 정지 문구입니다. 모든 오류에 동일한 붉은 처리가 있는 것은 아닙니다. 인스펙터 오류는 기본 글자색의 alert입니다. 마을의 잔디·길·건축물 색은 에셋/장면 표현이지 작업 UI의 추가 조작색이 아닙니다.

## Typography

실무적인 시스템 글꼴 스택을 본문·버튼·입력에 사용합니다. 마케팅용 display 글꼴이나 외부 웹폰트는 없습니다. 제목은 headline/title, 소제목·입력 라벨은 label, JSON은 code 역할입니다. 본문 문단 최대 폭은 (75ch), 보고서 원문은 (90ch), 글자 크기 (14px), 줄높이 (1.9)입니다. 작은 안내는 주로 (11–12px), 말풍선 힌트는 (9px), 좁은 화면에서는 (8px)입니다. 폭 (780px) 이하에서 브랜드 제목은 (17px), 상세 JSON은 (12px)입니다.

## Layout

상단 제목·모드 전환·`모의 응답 · 실제 GPT 호출 없음` 고지 아래, 최대 폭 (1440px)의 본문을 둡니다. 기본 좌우 여백 (28px), 작업 영역 간격 (24px), 오른쪽 상세 폭 (320px)입니다. 폭 (1400px) 이상에서는 상세 (350px), (1050px) 이하에서는 상세 (290px)·간격 (16px)입니다. (780px) 이하에서는 장면과 조작 → 상세 → 입력 순으로 쌓고 본문 여백을 (18px 14px 0)으로 바꿉니다.

마을 비율은 (8/5), 내부 Pixi 캔버스는 (768×480), 16px 타일을 3배 world scale로 배치합니다. 하단 composer·보고서·사건 목록은 일반 문서 흐름입니다. 고정 오버레이가 아닙니다. 긴 JSON은 감싸서 표시하고 인스펙터 높이를 제한하지 않으므로 넓은 창에서도 입력까지 내려가는 페이지 높이가 커질 수 있습니다. 휴대폭에서는 전송 버튼이 입력 아래 오른쪽에 놓입니다.

## Elevation & Depth

기본 패널은 단색 바탕과 얇은 경계로 구분합니다. 말풍선만 부드러운 오프셋 그림자 (`0 3px 8px #2e4e2420`)와 작은 꼬리를 가집니다. 캐릭터 아래에는 장면 코드의 타원 그림자 (`0x334c32`, alpha `.24`)가 있습니다. 유리 효과나 대형 떠 있는 카드 체계는 없습니다.

## Shapes

작은 라벨·코드 영역, 입력·탐색, 패널, 말풍선의 반경은 frontmatter를 따릅니다. 상태 표식은 pill (20px), 역할 점은 원형입니다. 마을은 경계를 clip합니다. 타일 원본은 (192×176px), 정면 인물 여섯 개는 각각 (16×16px)이며 Kenney Tiny Town / Roguelike Characters, CC0 1.0입니다. Pixi `nearest`, `antialias:false`, `roundPixels:true`와 CSS `image-rendering:pixelated`로 픽셀 경계를 유지합니다. 작업대·종이는 코드 도형입니다.

## Components

- **Buttons:** 일반 버튼 최소 높이 (38px), 얇은 경계. hover·active·disabled를 구분하고 전송은 녹색입니다. quiet는 투명 바탕, 취소는 짙은 적갈색 글자입니다. 공통 키보드 focus는 (3px) outline과 (3px) offset입니다. 전송에는 별도 active 덮어쓰기가 없으며 hover가 우선합니다.
- **Textarea:** 세로 크기 조절, 최소 높이 (68px)·최대 (300px). placeholder는 명시된 옅은 색입니다. 오류 시 입력을 보존하고 alert를 아래 표시합니다. textarea 자체의 전용 오류 테두리나 disabled 스타일은 없습니다. 비어 있거나 전송 중이면 전송 버튼이 비활성입니다.
- **Navigation:** 두 텍스트 링크, 현재 모드는 `aria-current`의 옅은 녹색 바탕과 굵기 (650). 별도 hover/active 색은 없고 공통 focus만 있습니다.
- **Speech bubble:** 전체가 버튼입니다. 본문은 두 줄로 제한하고 오른쪽 아래 `(클릭해서 더 자세히)`를 opacity `.76`으로 표시합니다. 작업대 오브젝트 본문은 정확히 `...`입니다. 사건이 없으면 disabled이며, hover/active는 일반 버튼 규칙을 상속합니다.
- **Inspector / records:** 선택 전 안내, 조회 중 status, 선택한 사건의 메타데이터·입력·출력·오류를 표시합니다. 새 사건이 도착해도 선택은 바뀌지 않습니다. 서버 기록과 로컬 장면 기록, 현재 버전과 이전 문서 표시를 구별합니다. 빈 큐·미제출 문서·빈 기록에도 설명이 있습니다.
- **Motion:** 버튼 배경·테두리 전환은 `.12s`, reduced-motion에서는 해당 CSS 전환만 꺼집니다. 정면 스프라이트를 코드로 이동 (1500ms)·작업 표시 (1600ms)·문서 운반 (2200ms)합니다. 바운스와 작업대 alpha 변화는 장면 연출이며 걷기 연속 프레임이나 실제 모델 작업의 증거가 아닙니다. Pixi ticker의 reduced-motion 대응은 구현되어 있지 않습니다.

## Do's and Don'ts

### Do:
- **Do** 픽셀 경계와 실무적인 한국어 시스템 글꼴을 유지합니다.
- **Do** 선택한 사건의 상세와 현재 실행·이전 문서를 구분합니다.
- **Do** 합성 자료와 실제 모델 미호출 고지를 읽을 수 있게 유지합니다.

### Don't:
- **Don't** 코드 이동을 검증된 걷기 애니메이션이나 실제 추론으로 설명하지 않습니다.
- **Don't** 두 스파이크의 스타일 기록을 정식 제품·새 브랜드 승인으로 확대하지 않습니다.
