"""보존 교안의 코퍼스 기반 연구 계약."""


class ResearchContractError(ValueError):
    pass


import json
from pathlib import Path


def json_object(raw, operation):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ResearchContractError(f"{operation}: invalid JSON") from exc
    if not isinstance(value, dict):
        raise ResearchContractError(f"{operation}: JSON object required")
    return value


ROSTER = {
    "큰그림 담당": "주제의 윤곽을 잡고 핵심 사건과 인물을 간추린다",
    "시간순 담당": "사건을 일어난 순서대로 늘어놓고 언제였는지를 못 박는다",
    "인물 담당": "한 사람이 무엇에 관여했고 어떤 일을 겪었는지 좇는다",
    "원인 담당": "무엇이 무엇으로 이어졌는지 자료에 적힌 대로 따라간다",
    "비교 담당": "둘 이상을 같은 잣대로 견주어 어디가 다른지 정리한다",
}


def required_text(obj, key, operation):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResearchContractError(f"{operation}: nonempty {key} required")
    return value


def parse_plan(raw, corpus):
    obj = json_object(raw, "plan")
    title = required_text(obj, "제목", "plan")
    if "\n" in title or len(title) > 300:
        raise ResearchContractError("plan: invalid report heading")
    toc = obj.get("목차")
    if not isinstance(toc, list) or not 1 <= len(toc) <= 4:
        raise ResearchContractError("plan: 1..4 content sections required")
    seeds, names, plan = set(), set(), []
    for i, item in enumerate(toc):
        if not isinstance(item, dict):
            raise ResearchContractError("plan: section object required")
        name = required_text(item, "절", "plan")
        brief = required_text(item, "지시", "plan")
        role = required_text(item, "역할", "plan")
        seed = required_text(item, "시작문서", "plan")
        if role not in ROSTER or seed not in corpus.docs or seed in seeds or name in names:
            raise ResearchContractError("plan: invalid role/seed or duplicate section")
        seeds.add(seed)
        names.add(name)
        plan.append(
            {
                "section_id": f"section-{i + 1}",
                "title": name,
                "assignee": f"researcher-{i + 1}",
                "role": role,
                "seed": seed,
                "instruction": brief,
                "budget": 3,
                "round": 1,
                "order": i,
            }
        )
    return title, plan


def parse_section(raw):
    obj = json_object(raw, "section")
    body = required_text(obj, "본문", "section")
    if type(obj.get("충분")) is not bool or not isinstance(obj.get("부족"), str):
        raise ResearchContractError("section: exact bool and reason string required")
    if not obj["충분"] and not obj["부족"].strip():
        raise ResearchContractError("section: insufficient reason required")
    if obj["충분"] and obj["부족"].strip():
        raise ResearchContractError("section: contradictory sufficiency and deficit")
    return {"본문": body, "충분": obj["충분"], "부족": obj["부족"]}


def evaluate_report(report, sections, corpus, *, model_calls=0, coord_chars=0, sub_chars=0):
    """교안 지표 + 누락되었던 미등록/타절 인용 검사. 의미적 지지는 별도 원문 대조 대상."""
    import re
    from collections import Counter

    from 서버.보고서품질 import citation_keys, visible_markdown

    raw_report = report
    report = visible_markdown(raw_report)
    latest = {s["section_id"]: s for s in sections}
    pairs = {(sid, d) for sid, s in latest.items() for d in s["읽은문서"]}
    read = {d for _, d in pairs}
    citations = citation_keys(report)
    used = Counter(c for c in citations if c in corpus.docs)
    unknown = sorted(set(citations) - set(corpus.docs))
    unread = sorted((set(citations) & set(corpus.docs)) - read)
    misattributed = []
    for sid, section in latest.items():
        bad = sorted(
            set(citation_keys(visible_markdown(section["본문"]))) - set(section["읽은문서"])
        )
        if bad:
            misattributed.append({"section_id": sid, "documents": bad})
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", report) if len(x.strip()) > 15]
    grounded = sum(any(c in corpus.docs for c in citation_keys(s)) for s in sentences)
    metrics = {
        "문장수": len(sentences),
        "근거붙은문장": grounded,
        "근거율": round(100 * grounded / max(len(sentences), 1), 1),
        "허위인용": sorted(set(unknown) | set(unread)),
        "읽은문서": len(read),
        "읽고안쓴": sorted(read - set(used)),
        "인용편중": round(100 * max(used.values()) / sum(used.values()), 1) if used else 0.0,
        "중복률": round(100 * (1 - len(read) / len(pairs)), 1) if pairs else 0.0,
        "절수": len(latest),
        "보고서자수": len(report),
        "인용수": sum(used.values()),
        "LLM호출": model_calls,
        "격리율": round(100 * coord_chars / max(coord_chars + sub_chars, 1), 1),
    }
    reasons = []
    body_markdown = (
        raw_report.split("\n", 1)[1].split("\n\n## 출처 및 이용허락", 1)[0]
        if "\n" in raw_report
        else ""
    )
    body = visible_markdown(body_markdown)
    if not re.match(r"^# [^\n]+\n", raw_report) or not body.strip():
        reasons.append("title_or_body_missing")
    if len(body.strip()) < 2000:
        reasons.append("body_under_2000")
    if not citations:
        reasons.append("citations_missing")
    if unknown or unread or misattributed:
        reasons.append("citation_integrity_error")
    warnings = ["자동 인용 검사는 문장과 원문 사이의 의미적 지지를 증명하지 않습니다."]
    if metrics["인용편중"] >= 50:
        warnings.append("인용 편중: 같은 문서의 인용 비율이 높습니다.")
    if metrics["중복률"]:
        warnings.append("조사관 사이에 중복 읽기가 있습니다.")
    if metrics["읽고안쓴"]:
        warnings.append("읽었지만 본문 인용에 쓰이지 않은 문서가 있습니다.")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "warnings": warnings,
        "metrics": metrics,
        "unknown_citations": unknown,
        "unread_citations": unread,
        "misattributed_citations": misattributed,
    }


def role_sentence(assignment):
    role = assignment["role"]
    return f"너는 {role}이다. {ROSTER[role]}."


def plan_prompt(question, corpus, prior_report=None):
    roster = "\n".join(f"- {k}: {v}" for k, v in ROSTER.items())
    system = (
        "너는 리서치 팀의 코디네이터다. 아래 질문에 답하는 보고서의 목차를 짜고, 각 절을 리서처 에이전트 "
        "하나에게 맡긴다. 절은 최대 4개.\n"
        "목차는 반드시 **내용 단위**로 나눠라 — 사건·시기·인물·쟁점처럼 질문이 실제로 묻는 "
        "덩어리로 쪼갠다. '개요·연표·인물·인과분석' 같은 형식 단위로 나누지 마라. 그렇게 나누면 "
        "네 리서처 에이전트가 같은 문서를 네 번 읽는다.\n"
        "절마다 그 절을 쓰는 데 가장 먼저 읽어야 할 문서를 문서 목록에서 하나 배정하라. "
        "절끼리 다른 문서를 주어라.\n"
        f"절의 성격에 맞는 리서처 에이전트를 명단에서 골라 붙여라.\n[리서처 에이전트 명단]\n{roster}\n"
        'JSON으로만: {"제목":"보고서 제목","목차":[{"절":"절 제목","지시":"이 절에서 '
        '밝혀야 할 것 한두 문장","역할":"명단에 있는 이름 그대로","시작문서":"목록에 있는 제목"}]}'
    )
    user = f"[질문 및 같은 실행의 누적 지시] {question}\n[읽을 수 있는 문서 카드]\n{corpus.cards()}"
    if prior_report:
        user += f"\n[이전 보고서 문맥 — 코퍼스 근거가 아니며 인용 근거로 사용 금지]\n{prior_report}"
    return system, user


def selection_prompt(assignment, read, candidates):
    return (
        role_sentence(assignment)
        + " 맡은 절을 쓰기 위해 다음에 읽을 문서를 후보 중에서 정확히 하나 "
        "고른다. 더 읽을 것이 없으면 그만둔다.\n"
        'JSON으로만: {"문서":"후보에 있는 제목"} 또는 {"문서":null}',
        (
            f"[맡은 절] {assignment['title']}\n[지시] {assignment['instruction']}\n"
            f"[이미 읽음] {', '.join(read) or '없음'}\n[후보] {', '.join(candidates)}"
        ),
    )


def read_prompt(title, brief, corpus):
    return (
        "너는 자료 조사 담당이다. 아래 문서를 읽고 지시에 관련된 내용만 여섯 문장 이내로 "
        "간추려라. 날짜/달력·수치/단위·출처 간 충돌과 짧은 직접 인용을 원문 그대로 보존하라. "
        "관련 없으면 '관련 없음'이라고만 답하라.\n" + f"[지시] {brief}",
        f"[문서: {title}]\n{corpus.docs[title]}",
    )


def section_prompt(assignment, notes):
    """과거 응답 재현 전용. 정상 신규 초안은 보고서근거.build_draft_request를 쓴다."""
    from 서버.보고서품질 import EVIDENCE_INSTRUCTION

    material = "\n\n".join(f"[{d}]\n{n}" for d, n in notes) or "(읽은 자료 없음)"
    return (
        role_sentence(assignment) + " 아래 자료만 근거로 보고서의 한 절을 써라. "
        "여섯 문장 이상 열두 문장 이하, 600자 이상. 문장마다 근거가 된 문서 제목을 «문서명» "
        "으로 붙여라. 자료에 없는 내용은 쓰지 마라. 지시를 자료로 다 채우지 못했으면 "
        "충분을 false 로 하고 무엇이 비었는지 적어라.\n"
        'JSON으로만: {"본문":"...","충분":true,"부족":"","근거":[]}' + EVIDENCE_INSTRUCTION,
        f"[맡은 절] {assignment['title']}\n[지시] {assignment['instruction']}\n[모은 자료]\n{material}",
    )


def synthesis_prompt(question, title, sections):
    from 서버.보고서도식 import ELEMENTS

    # 완성된 예시를 쓴다. viewBox=... 같은 축약은 유효한 SVG 문법이 아니다.
    example = {
        "머리말": "확인된 내용의 요약",
        "맺음말": "확인된 범위와 한계",
        "표": [
            {
                "제목": "비교",
                "열": ["항목", "값"],
                "행": [{"셀": ["항목", "확인된 값"], "출처": ["자료 키"]}],
            }
        ],
        "도식": [
            {
                "제목": "확인된 관계",
                "설명": "제공된 근거 안의 관계만 표시한다.",
                "코드": (
                    "<svg viewBox='0 0 600 180' font-family='Malgun Gothic'>"
                    "<line x1='80' y1='90' x2='520' y2='90' stroke='#333333'/>"
                    "<text x='80' y='60' font-size='24'>항목</text></svg>"
                ),
                "출처": ["자료 키"],
            }
        ],
        "시각화생략사유": "",
    }
    outline = "\n\n".join(
        f"{i + 1}. {s['title']}\n{s['본문']}\n[검증된 근거]\n{json.dumps(s['근거'], ensure_ascii=False)}"
        for i, s in enumerate(sections)
    )
    return (
        (
            "너는 보고서를 마무리하는 편집자다. 아래는 리서처 에이전트들이 각자 쓴 완성된 절과 검증된 근거다. "
            "본문은 고치지 말고 머리말·맺음말을 각각 세 문장 이내로 써라. 검증된 근거 안에서만 선택적으로 "
            "최대2개 표(2~6열,1~10행,셀300자 이하)와 최대2개 SVG 도식을 작성하라. "
            "출처 열은 렌더러가 추가하므로 열/셀에 출처를 추가하지 마라. "
            "각 행의 출처 배열에만 실제 읽은 자료 키를 담고 모든 행의 셀 수를 열 수와 정확히 맞춰라. "
            "날짜·사건의 근거가 있으면 인과를 뜻하지 않는 SVG 연표나 병렬 카드도 고려하라. "
            "모든 도식을 인과 화살표로 해석하지 말고, 날짜·인과를 새로 만들지 마라. 근거 부족 시 생략은 허용한다. "
            "HTML·이미지 URL은 금지다. SVG는 viewBox와 기본 도형/text/title/desc만, "
            "script/style/image/use/href/이벤트/외부참조/DTD는 금지. UTF-8 32KiB·요소300·깊이12·path12000자 이하, 좌표절댓값100000 이하. "
            f"\n허용 SVG 요소: {', '.join(sorted(ELEMENTS))}.\n"
            "defs/marker/foreignObject/filter/id/class/role/aria 속성과 url(...) 참조도 금지다. "
            "속성의 숫자에는 px/% 단위를 붙이지 말고 색은 #RGB 또는 #RRGGBB로 써라. "
            "font-family를 쓰면 sans-serif 또는 Malgun Gothic 하나만 지정하라. "
            "SVG 속성은 예시처럼 작은따옴표로 감싸고 좌표/크기에는 실제 정수·소수만 써라. "
            "숫자 속성에 단어·설명·말줄임표를 쓰지 마라. 아래는 형식 예시이며 실제 값과 출처로 바꿔라. "
            "화살표는 line/path와 polygon으로 그리고, 가로 viewBox 600·font-size 24 이상을 권장한다. "
            "출처는 실제 읽은 자료 키만 쓰며 인과를 시간 순서로부터 만들지 마라. 근거가 없으면 표/도식을 생략하고 이유를 적어라.\n"
            "JSON으로만: " + json.dumps(example, ensure_ascii=False)
        ),
        f"[질문] {question}\n[보고서 제목] {title}\n[절 목록]\n{outline}",
    )


def assemble_report(title, introduction, conclusion, sections, corpus, visual_summary=None):
    from urllib.parse import quote

    from 서버.보고서품질 import visual_markdown

    parts = [f"# {title}", introduction]
    for i, section in enumerate(sections):
        parts.append(f"## {i + 1}. {section['title']}  _({section['role']})_\n\n{section['본문']}")
    parts.append(f"## 맺음말\n\n{conclusion}")
    parts.extend(visual_markdown(visual_summary))
    # 출처 목록에는 실제로 읽은 문서만. 인용 괄호는 본문의 지표만 세도록 사용하지 않는다.
    read = sorted({d for section in sections for d in section["읽은문서"]})
    provenance = [
        "## 출처 및 이용허락",
        f"고정 코퍼스: {corpus.source}; 수집일 {corpus.collected_at}.",
        (
            "한국어 위키백과 원문을 조사 메모로 요약하고 절 원고로 재구성했습니다. "
            "원문 기여자·변경 이력은 각 문서의 역사 탭에서 확인할 수 있습니다. "
            "원자료 이용허락: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). "
            "이 보고서는 같은 이용허락으로 제공합니다."
        ),
    ]
    provenance += [
        f"- [{d}](https://ko.wikipedia.org/wiki/{quote(d.replace(' ', '_'))}) "
        f"([기여 이력](https://ko.wikipedia.org/w/index.php?title={quote(d)}&action=history))"
        for d in read
    ]
    return "\n\n".join(parts + ["\n".join(provenance)])


class Corpus:
    def __init__(self, path):
        self._load(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_data(cls, data):
        instance = cls.__new__(cls)
        instance._load(data)
        return instance

    def _load(self, data):
        if not isinstance(data, dict):
            raise ResearchContractError("corpus: object required")
        docs, links = data.get("docs"), data.get("links")
        if not isinstance(docs, dict) or not docs or not isinstance(links, dict):
            raise ResearchContractError("corpus: docs/links required")
        if any(
            not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
            for k, v in docs.items()
        ):
            raise ResearchContractError("corpus: nonempty titles/text required")
        if any(
            k not in docs
            or not isinstance(v, list)
            or any(not isinstance(t, str) or t not in docs for t in v)
            for k, v in links.items()
        ):
            raise ResearchContractError("corpus: invalid link reference")
        self.docs, self.links = dict(docs), {k: list(v) for k, v in links.items()}
        self.source = str(data.get("_출처", "한국어 위키백과 (CC BY-SA 4.0)"))
        self.collected_at = str(data.get("_수집일", "미기재"))

    def candidates(self, read, avoid):
        used = set(read) | set(avoid)
        frontier = sorted({t for d in read for t in self.links.get(d, [])} - used)
        return (
            frontier
            or [t for t in self.docs if t not in used]
            or [t for t in self.docs if t not in read]
        )

    def pick(self, raw, candidates, read):
        obj = json_object(raw, "selection")
        if "문서" not in obj:
            raise ResearchContractError("selection: document field required")
        pick = obj["문서"]
        if pick is None:
            return None
        if (
            not isinstance(pick, str)
            or pick not in candidates
            or pick not in self.docs
            or pick in read
        ):
            raise ResearchContractError("selection: not an unread presented candidate")
        return pick

    def cards(self):
        return "\n".join(f"- {t}: {v[:250].replace(chr(10), ' ')}" for t, v in self.docs.items())
