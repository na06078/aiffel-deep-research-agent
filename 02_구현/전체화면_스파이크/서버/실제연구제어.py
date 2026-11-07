"""실제 코퍼스 6노드 감독자. 외부 제공자 호출은 주입 모델만 담당한다."""

import asyncio
import json
import operator
import re
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from 서버 import 웹연구
from 서버.교안연구 import (
    Corpus,
    ResearchContractError,
    assemble_report,
    evaluate_report,
    json_object,
    parse_plan,
    parse_section,
    plan_prompt,
    read_prompt,
    required_text,
    section_prompt,
    selection_prompt,
    synthesis_prompt,
)
from 서버.실행제어 import ExecutionService
from 서버.저장소 import encode, now, uid
from 서버.활동기록 import measured_call, measured_node, start_call


class SupersededResearch(Exception):
    """오래된 버전은 다음 외부 요청이나 현재 상태 변경을 할 수 없다."""


class RealState(TypedDict, total=False):
    source_mode: str
    run_id: str
    revision: int
    instruction: str
    question: str
    title: str
    plan: list
    pending: list
    round: int
    stop_reason: str
    assignment: dict
    prior: dict
    avoid: list
    results: Annotated[list, operator.add]
    draft: str


def latest_sections(results):
    latest = {}
    for section in results:
        sid = section["section_id"]
        if sid not in latest or section["round"] >= latest[sid]["round"]:
            latest[sid] = section
    return sorted(latest.values(), key=lambda section: section["order"])


def build_real_graph(service):
    graph = StateGraph(RealState)
    for name, method in [
        ("plan", service.plan),
        ("dispatch", service.dispatch),
        ("researcher", service.research),
        ("review", service.review),
        ("synthesize", service.synthesize),
        ("evaluate", service.evaluate),
    ]:
        graph.add_node(name, method)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch")

    def fanout(state):
        latest = {s["section_id"]: s for s in latest_sections(state["results"])}
        sends = []
        for assignment in state["pending"]:
            sid = assignment["section_id"]
            avoid = {a["seed"] for a in state["plan"] if a["section_id"] != sid and "seed" in a}
            avoid |= {d for other, s in latest.items() if other != sid for d in s["읽은문서"]}
            sends.append(
                Send(
                    "researcher",
                    {
                        "run_id": state["run_id"],
                        "revision": state["revision"],
                        "source_mode": state.get("source_mode", "local"),
                        "instruction": state["instruction"],
                        "question": state["question"],
                        "assignment": assignment,
                        "prior": latest.get(sid, {}),
                        "avoid": sorted(avoid),
                        "results": [],
                    },
                )
            )
        return sends or "review"

    graph.add_conditional_edges("dispatch", fanout, ["researcher", "review"])
    graph.add_edge("researcher", "review")
    graph.add_conditional_edges(
        "review", lambda s: "dispatch" if s["pending"] else "synthesize", ["dispatch", "synthesize"]
    )
    graph.add_edge("synthesize", "evaluate")
    graph.add_edge("evaluate", END)
    return graph.compile()


class RealResearchService(ExecutionService):
    def __init__(self, data_dir, model, corpus_path, *, web_client=None, web_store=None):
        self.web_client = web_client
        self._web_store = web_store
        corpus = Corpus(corpus_path)
        super().__init__(data_dir, provider="openai", mode="research")
        self.model = model
        self.corpus = corpus
        self.report_filename_prefix = "🧾 실제 연구 보고서"
        self.graph = build_real_graph(self)

    @property
    def source_modes(self):
        return ["local", "web"] if self.web_client is not None else ["local"]

    @property
    def web_store(self):
        if self._web_store is None:
            from 서버.웹자료저장소 import WebSourceStore

            self._web_store = WebSourceStore(self.repo.root / "웹자료/자료.sqlite3")
        return self._web_store

    def start(self, db, state, text, instruction_id, *, source_mode=None, raw_text=None):
        current = self.repo.run(state, state["current_run_id"])
        history = (
            list(current.get("instruction_history", [current["question"]]))
            if current and current["status"] == "running"
            else []
        )
        run, admission = super().start(
            db, state, text, instruction_id, source_mode=source_mode, raw_text=raw_text
        )
        run["instruction_history"] = history + [text]
        if run.get("source_mode", "local") == "web":
            run["web_usage"] = {
                "search_attempts": 0,
                "fetch_attempts": 0,
                "observed_http_requests": 0,
            }
        self.repo.save_revision(db, run)
        return run, admission

    def check(self, state):
        with self.repo.read_connection() as db:
            data = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            if not self.repo.fenced(data, state["run_id"], state["revision"]):
                raise SupersededResearch()

    async def supervise(self, run_id, revision, text):
        try:
            with self.repo.read_connection() as db:
                row = db.execute(
                    "SELECT body FROM revisions WHERE run_id=? AND revision=?", (run_id, revision)
                ).fetchone()
                source_mode = json.loads(row[0]).get("source_mode", "local")
            await self.graph.ainvoke(
                {
                    "run_id": run_id,
                    "revision": revision,
                    "instruction": text,
                    "results": [],
                    "source_mode": source_mode,
                }
            )
        except SupersededResearch:
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- 감독자가 소유한 작업의 최종 실패 경계.
            # 원시 제공자 오류/임의 예외 문자열은 저장하지 않는다.
            message = (
                str(error)
                if isinstance(error, ResearchContractError)
                else "연구 실행을 안전하게 중단했습니다."
            )
            with self.repo.transaction() as (db, data):
                run = self.repo.run(data, run_id)
                # complete가 이미 기록한 구체적 실패는 일반 감독자 오류로 덮지 않는다.
                if run and run["control_revision"] == revision and run["status"] == "failed":
                    return
                self.fail(db, data, run_id, revision, "dispatcher", message, "execution.failed")

    async def close(self):
        await super().close()
        try:
            await self.model.aclose()
        finally:
            if self.web_client is not None:
                await self.web_client.aclose()

    async def complete(
        self,
        state,
        actor,
        operation,
        prompts,
        *,
        inputs=None,
        json_mode=True,
        on_complete_receipt=None,
    ):
        self.check(state)
        system, user = prompts
        context = {
            "run_id": state["run_id"],
            "revision": state["revision"],
            "actor": actor,
            "operation": operation,
        }
        payload = {
            "system": system,
            "user": user,
            "context": context,
            "inputs": inputs,
            "max_output_tokens": 4096,
            "json_mode": json_mode,
        }
        call_id = uid()
        with self.repo.transaction() as (db, data):
            if not self.repo.fenced(data, state["run_id"], state["revision"]):
                raise SupersededResearch()
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?,?)",
                (
                    call_id,
                    state["run_id"],
                    state["revision"],
                    actor,
                    "running",
                    encode(payload),
                    None,
                    None,
                ),
            )
            start_call(db, state, actor, call_id, provider="openai")
        sent = []

        def on_attempt(attempt_id):
            # 동기 callback: 예약 차단/미전송 논리 호출을 실제 HTTP 시도와 혼동하지 않는다.
            with self.repo.transaction() as (db, data):
                run = self.repo.fenced(data, state["run_id"], state["revision"])
                if not run:
                    raise SupersededResearch()
                if sent:
                    raise ResearchContractError("attempt: unexpected automatic retry")
                sent.append(attempt_id)
                run["actual_model_requests"] = run.get("actual_model_requests", 0) + 1
                self.repo.save_revision(db, run)
                self.repo.event(
                    db,
                    state["run_id"],
                    state["revision"],
                    "model.started",
                    actor,
                    input=payload,
                    output={"call_id": call_id, "attempt_id": attempt_id},
                    status="running",
                    summary=f"{operation} · 실제 모델 요청 전송",
                )

        try:
            response = await measured_call(
                self.repo,
                call_id,
                self.model.complete(
                    system,
                    user,
                    context=context,
                    max_output_tokens=4096,
                    json_mode=json_mode,
                    on_attempt=on_attempt,
                ),
            )
            if not sent:
                raise ResearchContractError("attempt: missing transmission callback")
            if not isinstance(response, dict) or not isinstance(response.get("content"), str):
                raise ResearchContractError("model: content string required")
            if response.get("attempt_id") != sent[0]:
                raise ResearchContractError("attempt: response does not bind sent attempt")
            with self.repo.transaction() as (db, data):
                active = self.repo.fenced(data, state["run_id"], state["revision"])
                db.execute(
                    "UPDATE calls SET status=?, output=? WHERE call_id=?",
                    ("completed" if active else "superseded", encode(response), call_id),
                )
                self.repo.event(
                    db,
                    state["run_id"],
                    state["revision"],
                    "model.completed" if active else "result.superseded",
                    actor,
                    input=payload,
                    output={"call_id": call_id, **response},
                    status="completed" if active else "superseded",
                    summary=f"{operation} · 응답 기록",
                )
            if not active:
                raise SupersededResearch()
            if on_complete_receipt is not None:
                self.check(state)
                on_complete_receipt(call_id)
            return response["content"]
        except SupersededResearch:
            with self.repo.transaction() as (db, data):
                db.execute("UPDATE calls SET status='superseded' WHERE call_id=?", (call_id,))
            raise
        except asyncio.CancelledError:
            with self.repo.transaction() as (db, data):
                db.execute("UPDATE calls SET status='cancelled' WHERE call_id=?", (call_id,))
            raise
        except Exception as error:
            message = getattr(error, "safe_message", None)
            if not isinstance(message, str):
                message = (
                    str(error)
                    if isinstance(error, ResearchContractError)
                    else "모델 요청 실패: 비밀 없는 오류 정보만 보존합니다."
                )
            code = getattr(error, "code", "model_error")
            if not isinstance(code, str):
                code = "model_error"
            status = getattr(error, "status", None)
            if type(status) is not int:
                status = None
            attempt_id = getattr(error, "attempt_id", sent[0] if sent else None)
            if not isinstance(attempt_id, str):
                attempt_id = sent[0] if sent else None
            if code == "provider_http" and status == 401:
                message = "모델 인증이 거절되었습니다. 프로젝트 키 설정을 확인해 주세요."
            elif code == "provider_http" and status == 429:
                message = "모델 요청 한도 또는 잔액을 확인해 주세요. 큐를 정지했습니다."
            safe_error = {
                "code": code,
                "message": message,
                "status": status,
                "attempt_id": attempt_id,
            }
            with self.repo.transaction() as (db, data):
                db.execute(
                    "UPDATE calls SET status='failed', output=? WHERE call_id=?",
                    (
                        encode(safe_error),
                        call_id,
                    ),
                )
                self.fail(
                    db,
                    data,
                    state["run_id"],
                    state["revision"],
                    actor,
                    message,
                    payload={**payload, "call_id": call_id, "code": code, "sent": bool(sent)},
                    safe_error=safe_error,
                )
            raise ResearchContractError(f"{operation}: request failed safely") from error

    @measured_node("plan", "planner", "actor")
    async def plan(self, state):
        self.check(state)
        with self.repo.read_connection() as db:
            data = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            run = self.repo.run(data, state["run_id"])
            question = "\n".join(
                f"{i + 1}. {text}" for i, text in enumerate(run["instruction_history"])
            )
            prior = db.execute(
                "SELECT body FROM reports WHERE report_id=?", (run["parent_report_id"],)
            ).fetchone()
            previous = json.loads(prior[0])["markdown"] if prior else None
        web = state.get("source_mode", "local") == "web"
        prompts = (
            웹연구.plan_prompt(question, previous)
            if web
            else plan_prompt(question, self.corpus, previous)
        )
        raw = await self.complete(
            state,
            "planner",
            "plan",
            prompts,
            inputs={"instruction_history": question, "prior_report_is_primary_evidence": False},
        )
        title, plan = 웹연구.parse_plan(raw) if web else parse_plan(raw, self.corpus)
        with self.repo.transaction() as (db, data):
            run = self.repo.fenced(data, state["run_id"], state["revision"])
            if not run:
                raise SupersededResearch()
            run.update(plan=plan, phase="researching", report_title=title)
            self.repo.save_revision(db, run)
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "plan.created",
                "planner",
                input={"question": question},
                output=plan,
                recipient="dispatcher",
                summary=f"{title} · {len(plan)}절 기획",
            )
        return {"question": question, "title": title, "plan": plan, "pending": plan, "round": 1}

    @measured_node("dispatch", "dispatcher", "code")
    async def dispatch(self, state):
        self.check(state)
        with self.repo.transaction() as (db, data):
            if not self.repo.fenced(data, state["run_id"], state["revision"]):
                raise SupersededResearch()
            for assignment in state["pending"]:
                self.repo.event(
                    db,
                    state["run_id"],
                    state["revision"],
                    "assignment.created",
                    "dispatcher",
                    input=assignment,
                    output=assignment,
                    recipient=assignment["assignee"],
                    summary=f"{assignment['title']} · {state['round']}라운드 조사 배정",
                )
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "instruction.applied",
                "dispatcher",
                input={"instruction": state["question"]},
                output=state["pending"],
                status="applied",
                summary=f"{state['round']}라운드 · {len(state['pending'])}절 병렬 파견",
            )
        return {}

    @measured_node("research", None, "actor")
    async def research(self, state):
        self.check(state)
        if state.get("source_mode", "local") == "web":
            section = await 웹연구.research(self, state)
            self.check(state)
            with self.repo.transaction() as (db, data):
                if not self.repo.fenced(data, state["run_id"], state["revision"]):
                    raise SupersededResearch()
                self.repo.event(
                    db,
                    state["run_id"],
                    state["revision"],
                    "research.completed",
                    section["assignee"],
                    input={"assignment": state["assignment"]},
                    output=section,
                    recipient="reviewer",
                    summary=f"{section['title']} · {len(section['읽은문서'])}웹자료",
                )
            return {"results": [section]}
        assignment = state["assignment"]
        actor = assignment["assignee"]
        read = list(state["prior"].get("읽은문서", []))
        notes = list(state["prior"].get("메모", []))
        title = assignment["seed"] if assignment["seed"] not in read else None
        for _ in range(assignment["budget"]):
            self.check(state)
            if title is None:
                candidates = self.corpus.candidates(read, state["avoid"])[:60]
                if not candidates:
                    break
                raw = await self.complete(
                    state,
                    actor,
                    "select",
                    selection_prompt(assignment, read, candidates),
                    inputs={"candidates": candidates, "read": read, "avoid": state["avoid"]},
                )
                title = self.corpus.pick(raw, candidates, read)
            if title is None:
                break
            prompts = read_prompt(title, assignment["instruction"], self.corpus)
            self.tool_event(
                state,
                "tool.started",
                {
                    "document": title,
                    "brief": assignment["instruction"],
                    "document_text": self.corpus.docs[title],
                },
                None,
                "running",
            )
            note = await self.complete(
                state,
                actor,
                "read_one",
                prompts,
                inputs={"document": title, "brief": assignment["instruction"]},
                json_mode=False,
            )
            if not note.strip():
                raise ResearchContractError("read_one: empty note")
            self.tool_event(
                state, "tool.completed", {"document": title}, {"note": note}, "completed"
            )
            read.append(title)
            notes.append([title, note])
            title = None
        raw = await self.complete(
            state,
            actor,
            "draft_section",
            section_prompt(assignment, notes),
            inputs={"assignment": assignment, "notes": notes},
        )
        section = {**assignment, **parse_section(raw), "읽은문서": read, "메모": notes}
        section["인용"] = re.findall(r"«([^»]+)»", section["본문"])
        section["허위인용"] = sorted(set(section["인용"]) - set(read))
        self.check(state)
        with self.repo.transaction() as (db, data):
            if not self.repo.fenced(data, state["run_id"], state["revision"]):
                raise SupersededResearch()
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "research.completed",
                actor,
                input={"assignment": assignment},
                output=section,
                recipient="reviewer",
                summary=f"{assignment['title']} · {len(read)}문서 · {'충분' if section['충분'] else '추가 조사 필요'}",
            )
        return {"results": [section]}

    def tool_event(self, state, kind, inputs, output, status):
        with self.repo.transaction() as (db, data):
            if not self.repo.fenced(data, state["run_id"], state["revision"]):
                raise SupersededResearch()
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                kind,
                "tool",
                input=inputs,
                output=output,
                status=status,
                recipient=state["assignment"]["assignee"],
                summary=f"read_one · {inputs['document']} · 문서 읽기",
            )

    @measured_node("review", "reviewer", "code")
    async def review(self, state):
        self.check(state)
        latest = {s["section_id"]: s for s in latest_sections(state["results"])}
        if set(latest) != {a["section_id"] for a in state["plan"]}:
            raise ResearchContractError("review: missing section result")
        gaps = [a for a in state["plan"] if not latest[a["section_id"]]["충분"]]
        pending = []
        reason = (
            "sufficient"
            if not gaps
            else "round_exhausted"
            if state["round"] >= 2
            else "redelegation"
        )
        if reason == "redelegation":
            for assignment in gaps:
                prior = latest[assignment["section_id"]]
                instruction = (
                    f"{assignment['instruction']} (재위임: 지난번에 «{'», «'.join(prior['읽은문서']) or '없음'}» "
                    f"를 읽었지만 {prior['부족']}. 그 빈 칸을 채우도록 아직 안 본 문서를 찾아라)"
                )
                pending.append(
                    {**assignment, "instruction": instruction, "round": state["round"] + 1}
                )
        updates = {a["section_id"]: a for a in pending}
        plan = [updates.get(a["section_id"], a) for a in state["plan"]]
        output = {
            "accepted_sections": len(latest),
            "insufficient_sections": [a["section_id"] for a in gaps],
            "pending": pending,
            "stop_reason": reason,
            "round": state["round"],
        }
        with self.repo.transaction() as (db, data):
            run = self.repo.fenced(data, state["run_id"], state["revision"])
            if not run:
                raise SupersededResearch()
            run.update(plan=plan, research_stop_reason=reason)
            self.repo.save_revision(db, run)
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "review.completed",
                "reviewer",
                input=latest_sections(state["results"]),
                output=output,
                recipient="dispatcher" if pending else "editor",
                summary=f"점검 · {reason} · 부족 {len(gaps)}절",
            )
        return {
            "plan": plan,
            "pending": pending,
            "round": state["round"] + bool(pending),
            "stop_reason": reason,
        }

    @measured_node("synthesize", "editor", "actor")
    async def synthesize(self, state):
        self.check(state)
        sections = latest_sections(state["results"])
        raw = await self.complete(
            state,
            "editor",
            "synthesize",
            synthesis_prompt(state["question"], state["title"], sections),
            inputs={
                "title": state["title"],
                "outline": [{"title": s["title"], "opening": s["본문"][:90]} for s in sections],
            },
        )
        obj = json_object(raw, "synthesize")
        introduction = required_text(obj, "머리말", "synthesize")
        conclusion = required_text(obj, "맺음말", "synthesize")
        if state.get("source_mode", "local") == "web":
            _, sections, sources = 웹연구.evidence(self, state, sections)
            draft = 웹연구.assemble(state["title"], introduction, conclusion, sections, sources)
        else:
            draft = assemble_report(state["title"], introduction, conclusion, sections, self.corpus)
        with self.repo.transaction() as (db, data):
            run = self.repo.fenced(data, state["run_id"], state["revision"])
            if not run:
                raise SupersededResearch()
            run.update(draft=draft, phase="evaluating")
            self.repo.save_revision(db, run)
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "draft.created",
                "editor",
                input={"section_ids": [s["section_id"] for s in sections]},
                output=draft,
                summary=f"{len(sections)}절 원고 원형 보존 · 초안 {len(draft)}자",
            )
        return {"draft": draft}

    @measured_node("evaluate", "evaluator", "code")
    async def evaluate(self, state):
        self.check(state)
        sections = latest_sections(state["results"])
        corpus = self.corpus
        provenance = []
        if state.get("source_mode", "local") == "web":
            corpus, sections, sources = 웹연구.evidence(self, state, sections)
            for source_id in sorted({d for section in sections for d in section["읽은문서"]}):
                source = sources[source_id]
                provenance.append(
                    {
                        **{
                            key: source[key]
                            for key in (
                                "source_id",
                                "url",
                                "final_url",
                                "title",
                                "text_sha256",
                                "fetched_at",
                                "published_at",
                            )
                        },
                        "read_text_sha256": 웹연구.digest(source["text"][:20000]),
                    }
                )
        with self.repo.transaction() as (db, data):
            run = self.repo.fenced(data, state["run_id"], state["revision"])
            if not run:
                raise SupersededResearch()
            rows = db.execute(
                "SELECT actor,input FROM calls WHERE run_id=? AND revision=?",
                (state["run_id"], state["revision"]),
            ).fetchall()
            chars = {"coord": 0, "sub": 0}
            for row in rows:
                chars["coord" if row["actor"] in {"planner", "editor"} else "sub"] += len(
                    json.loads(row["input"])["user"]
                )
            quality = evaluate_report(
                state["draft"],
                sections,
                corpus,
                model_calls=run["actual_model_requests"],
                coord_chars=chars["coord"],
                sub_chars=chars["sub"],
            )
            if state.get("source_mode", "local") == "web" and any(
                not s["읽은문서"] or s.get("code_origin_insufficiency") for s in sections
            ):
                quality["reasons"].append("web_sources_insufficient")
                quality["passed"] = False
            run["quality"] = quality
            if state.get("source_mode", "local") == "web":
                run["source_provenance"] = provenance
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "quality.evaluated",
                "evaluator",
                input={"draft": state["draft"]},
                output=quality,
                summary=f"필수 검사 {'통과' if quality['passed'] else '불합격'} · 인용 오류 {len(quality['unknown_citations']) + len(quality['unread_citations'])}",
            )
            if quality["passed"]:
                self.report_title = state["title"]
                self.publish(db, data, state["run_id"], state["revision"])
            else:
                run.update(status="blocked", phase="quality_failed", updated_at=now())
                data.update(queue_paused=True, queue_pause_reason="quality_failed")
            self.repo.save_revision(db, run)
        if quality["passed"]:
            self.start_next_queue()
        return {}
