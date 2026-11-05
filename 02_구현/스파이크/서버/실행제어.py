"""앱 소유 감독자: 브라우저 연결과 독립된 graph/task/gate 수명."""

import asyncio
import hashlib
import json

from 서버.모델대역 import FakeModel
from 서버.시험문서 import DOCUMENTS, evaluate_candidate
from 서버.연구그래프 import build_graph
from 서버.저장소 import ApiError, Repository, encode, now, uid


class ExecutionService:
    def __init__(self, data_dir):
        self.repo = Repository(data_dir)
        self.repo.restore()
        self.model = FakeModel()
        self.gates = {}
        self.tasks = set()
        self.graph = build_graph(self)

    def spawn(self, run_id, revision, text):
        task = asyncio.create_task(self.supervise(run_id, revision, text))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def supervise(self, run_id, revision, text):
        try:
            await self.graph.ainvoke(
                {"run_id": run_id, "revision": revision, "instruction": text, "results": []}
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- 감독자는 소유 작업 오류를 저널에 보존한다.
            with self.repo.transaction() as (db, data):
                self.fail(db, data, run_id, revision, "dispatcher", str(error), "execution.failed")

    def fail(self, db, data, run_id, revision, actor, message, kind="model.failed", payload=None):
        active = self.repo.fenced(data, run_id, revision)
        self.repo.event(
            db,
            run_id,
            revision,
            kind if active else "result.superseded",
            actor,
            input=payload,
            error={"code": "model_error", "message": message},
            status="failed" if active else "superseded",
        )
        if active:
            active.update(status="failed", phase="failed", updated_at=now())
            data.update(queue_paused=True, queue_pause_reason="model_error")
            self.repo.save_revision(db, active)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.gates.clear()

    def admit(self, text, request_id):
        raw_text = text
        text = text.strip()
        queued_command = text == "/큐" or text.startswith("/큐 ")
        instruction = text[2:].strip() if queued_command else text
        instruction_id = uid()
        body = {"action": "commands", "text": raw_text}
        with self.repo.transaction() as (db, state):
            duplicate = self.repo.duplicate(db, request_id, body)
            if duplicate is not None:
                return duplicate
            if not instruction:
                raise ApiError(422, "empty_instruction", "지시 내용을 입력해 주세요.")
            run = self.repo.run(state, state["current_run_id"])
            if queued_command and (state["queue_paused"] or run and run["status"] == "running"):
                state["queue"].append(
                    {"instruction_id": instruction_id, "text": instruction, "status": "queued"}
                )
                admission = {
                    "instruction_id": instruction_id,
                    "run_id": None,
                    "mode": "queued",
                    "status": "queued",
                    "control_revision": None,
                }
                self.repo.event(
                    db,
                    run["run_id"] if run else "queue",
                    run["control_revision"] if run else 0,
                    "instruction.queued",
                    "planner",
                    input={"text": text},
                    output=admission,
                    status="queued",
                )
                self.repo.remember(db, request_id, body, admission)
                return admission
            run, admission = self.start(db, state, instruction, instruction_id)
            self.repo.remember(db, request_id, body, admission)
        self.spawn(run["run_id"], run["control_revision"], instruction)
        return admission

    def start(self, db, state, text, instruction_id):
        run = self.repo.run(state, state["current_run_id"])
        if run and run["status"] == "running":
            self.repo.save_revision(db, run)
            run["control_revision"] += 1
            run.update(
                question=text,
                plan=[],
                draft=None,
                report_id=None,
                quality={"passed": None, "reasons": []},
                phase="planning",
                updated_at=now(),
            )
        else:
            previous = next(
                (r["report_id"] for r in reversed(state["runs"]) if r["status"] == "completed"),
                None,
            )
            run = {
                "run_id": uid(),
                "question": text,
                "control_revision": 1,
                "status": "running",
                "phase": "planning",
                "plan": [],
                "report_id": None,
                "draft": None,
                "quality": {"passed": None, "reasons": []},
                "parent_report_id": previous,
                "created_at": now(),
                "updated_at": now(),
                "mock_requests": 0,
                "actual_model_requests": 0,
            }
            state["runs"].append(run)
            state["current_run_id"] = run["run_id"]
        admission = {
            "instruction_id": instruction_id,
            "run_id": run["run_id"],
            "mode": "immediate",
            "status": "received",
            "control_revision": run["control_revision"],
        }
        self.repo.event(
            db,
            run["run_id"],
            run["control_revision"],
            "instruction.received",
            "planner",
            input={"text": text},
            output=admission,
            status="received",
        )
        self.repo.save_revision(db, run)
        return run, admission

    def start_next_queue(self):
        with self.repo.transaction() as (db, data):
            run = self.take_next(db, data)
        if not run:
            return None
        self.spawn(run["run_id"], run["control_revision"], run["question"])
        return run["run_id"]

    def take_next(self, db, data):
        current = self.repo.run(data, data["current_run_id"])
        if data["queue_paused"] or not data["queue"] or current and current["status"] == "running":
            return None
        item = data["queue"].pop(0)
        return self.start(db, data, item["text"], item["instruction_id"])[0]

    def call_start(self, state, actor, payload, gate=None):
        with self.repo.transaction() as (db, data):
            run = self.repo.fenced(data, state["run_id"], state["revision"])
            if not run:
                return None
            call_id = uid()
            run["mock_requests"] += 1
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?,?)",
                (
                    call_id,
                    state["run_id"],
                    state["revision"],
                    actor,
                    "waiting" if gate else "running",
                    encode(payload),
                    None,
                    encode(gate) if gate else None,
                ),
            )
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "model.started",
                actor,
                input=payload,
                output={"gate_id": gate["gate_id"]} if gate else None,
                status="waiting" if gate else "running",
            )
        return call_id

    async def plan(self, state):
        payload = {"instruction": state["instruction"], "synthetic": True}
        with self.repo.transaction() as (db, data):
            run = self.repo.run(data, state["run_id"])
            previous = db.execute(
                "SELECT body FROM reports WHERE report_id=?", (run["parent_report_id"],)
            ).fetchone()
            payload["prior_report_context"] = json.loads(previous[0]) if previous else None
            payload["prior_report_is_primary_evidence"] = False
        call_id = self.call_start(state, "planner", payload)
        if call_id is None:
            return {"plan": []}
        plan = await self.model.plan(payload)
        with self.repo.transaction() as (db, data):
            db.execute(
                "UPDATE calls SET status='completed',output=? WHERE call_id=?",
                (encode(plan), call_id),
            )
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "model.completed",
                "planner",
                input=payload,
                output=plan,
            )
            run = self.repo.fenced(data, state["run_id"], state["revision"])
            if not run:
                self.repo.event(
                    db,
                    state["run_id"],
                    state["revision"],
                    "result.superseded",
                    "planner",
                    output=plan,
                    status="superseded",
                )
                return {"plan": []}
            run["plan"] = plan
            run["phase"] = "researching"
            self.repo.save_revision(db, run)
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "plan.created",
                "planner",
                input=payload,
                output=plan,
                recipient="dispatcher",
            )
        return {"plan": plan}

    async def dispatch(self, state):
        with self.repo.transaction() as (db, data):
            if not self.repo.fenced(data, state["run_id"], state["revision"]):
                return {"plan": []}
            for assignment in state["plan"]:
                self.repo.event(
                    db,
                    state["run_id"],
                    state["revision"],
                    "assignment.created",
                    "dispatcher",
                    input=assignment,
                    output=assignment,
                    recipient=assignment["assignee"],
                )
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "instruction.applied",
                "dispatcher",
                input={"instruction": state["instruction"]},
                output=state["plan"],
                status="applied",
            )
        return {}

    async def research(self, state):
        assignment = state["assignment"]
        gate_id = uid()
        future = asyncio.get_running_loop().create_future()
        self.gates[gate_id] = future
        gate = {
            "gate_id": gate_id,
            "run_id": state["run_id"],
            "control_revision": state["revision"],
            "actor": assignment["assignee"],
            "section_id": assignment["section_id"],
            "created_at": now(),
            "is_stale": False,
        }
        payload = {"instruction": state["instruction"], "assignment": assignment, "synthetic": True}
        payload["document_id"] = f"합성문서-{assignment['assignee'].split('-')[-1]}"
        payload["source"] = DOCUMENTS[payload["document_id"]]
        call_id = self.call_start(state, assignment["assignee"], payload, gate)
        if call_id is None:
            self.gates.pop(gate_id, None)
            future.cancel()
            return {"results": []}
        try:
            output = await self.model.research(future, payload)
            with self.repo.transaction() as (db, data):
                db.execute(
                    "UPDATE calls SET status='completed',output=? WHERE call_id=?",
                    (encode(output), call_id),
                )
                active = self.repo.fenced(data, state["run_id"], state["revision"])
                self.repo.event(
                    db,
                    state["run_id"],
                    state["revision"],
                    "model.completed" if active else "result.superseded",
                    assignment["assignee"],
                    input=payload,
                    output=output,
                    status="completed" if active else "superseded",
                )
            return {"results": [output]}
        except RuntimeError as error:
            with self.repo.transaction() as (db, data):
                db.execute(
                    "UPDATE calls SET status='failed',output=? WHERE call_id=?",
                    (encode({"error": str(error)}), call_id),
                )
                self.fail(
                    db,
                    data,
                    state["run_id"],
                    state["revision"],
                    assignment["assignee"],
                    str(error),
                    payload=payload,
                )
            return {"results": []}
        finally:
            self.gates.pop(gate_id, None)

    async def review(self, state):
        with self.repo.transaction() as (db, data):
            if self.repo.fenced(data, state["run_id"], state["revision"]):
                self.repo.event(
                    db,
                    state["run_id"],
                    state["revision"],
                    "review.completed",
                    "reviewer",
                    input=state["results"],
                    output={"accepted_sections": len(state["results"])},
                    recipient="editor",
                )
        return {}

    async def synthesize(self, state):
        with self.repo.transaction() as (db, data):
            if not self.repo.fenced(data, state["run_id"], state["revision"]):
                return {}
        payload = {"instruction": state["instruction"], "results": state["results"]}
        call_id = self.call_start(state, "editor", payload)
        if call_id is None:
            return {}
        draft = await self.model.synthesize(payload)
        with self.repo.transaction() as (db, data):
            db.execute(
                "UPDATE calls SET status='completed',output=? WHERE call_id=?",
                (encode(draft), call_id),
            )
            run = self.repo.fenced(data, state["run_id"], state["revision"])
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "draft.created" if run else "result.superseded",
                "editor",
                input=payload,
                output=draft,
                status="completed" if run else "superseded",
            )
            if run:
                run["draft"] = draft
                run["phase"] = "evaluating"
                self.repo.save_revision(db, run)
        return {"draft": draft}

    async def evaluate(self, state):
        with self.repo.transaction() as (db, data):
            run = self.repo.fenced(data, state["run_id"], state["revision"])
            if not run or not state.get("draft"):
                return {}
            quality = evaluate_candidate(state["draft"])
            run["quality"] = quality
            self.repo.event(
                db,
                state["run_id"],
                state["revision"],
                "quality.evaluated",
                "evaluator",
                input={"draft": state["draft"]},
                output=quality,
            )
            if quality["passed"]:
                self.publish(db, data, state["run_id"], state["revision"])
            else:
                run["status"] = "blocked"
                run["phase"] = "quality_failed"
                data["queue_paused"] = True
                data["queue_pause_reason"] = "quality_failed"
            self.repo.save_revision(db, run)
        if quality["passed"]:
            self.start_next_queue()
        return {}

    def publish(self, db, data, run_id, revision):
        # 파일 쓰기·최종 참조·제출 모두 같은 commit 경계에서 버전/종료 상태를 검사한다.
        run = self.repo.fenced(data, run_id, revision)
        if not run:
            return
        report_id = uid()
        filename = f"🧪 스파이크 보고서_{report_id}.md"
        content = run["draft"].encode("utf-8")
        path = self.repo.root / filename
        path.write_bytes(content)
        checked = path.read_bytes()
        if checked != content:
            raise RuntimeError("보고서 파일 재열기 검증 실패")
        report = {
            "report_id": report_id,
            "run_id": run_id,
            "control_revision": revision,
            "title": "합성 실행 제어 시험 보고서",
            "markdown": checked.decode("utf-8"),
            "sha256": hashlib.sha256(checked).hexdigest(),
            "created_at": now(),
            "download_url": f"/api/spike/reports/{report_id}/download",
        }
        db.execute("INSERT INTO reports VALUES(?,?,?)", (report_id, encode(report), filename))
        self.repo.event(
            db,
            run_id,
            revision,
            "report.submitted",
            "publisher",
            input={"draft": run["draft"], "quality": run["quality"]},
            output=report,
        )
        run.update(status="completed", phase="submitted", report_id=report_id, updated_at=now())

    def report_bytes(self, report_id):
        with self.repo.connection() as db:
            row = db.execute(
                "SELECT body,filename FROM reports WHERE report_id=?", (report_id,)
            ).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "최종 보고서가 없습니다.")
            report = json.loads(row["body"])
            path = self.repo.root / row["filename"]
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise ApiError(
                    409, "report_integrity_error", "정본 파일을 읽을 수 없습니다."
                ) from exc
            if hashlib.sha256(content).hexdigest() != report["sha256"] or content != report[
                "markdown"
            ].encode("utf-8"):
                raise ApiError(409, "report_integrity_error", "정본 파일 해시가 다릅니다.")
            return report, content, row["filename"]

    def release(self, gate_ids, outcome):
        released = []
        with self.repo.transaction() as (db, _data):
            rows = []
            for gate_id in dict.fromkeys(gate_ids):
                row = db.execute(
                    "SELECT * FROM calls WHERE json_extract(gate,'$.gate_id')=?", (gate_id,)
                ).fetchone()
                if row is None:
                    raise ApiError(404, "gate_not_found", "존재하지 않는 대역 gate입니다.")
                rows.append((gate_id, row))
            for gate_id, row in rows:
                future = self.gates.get(gate_id)
                if row["status"] != "waiting" or future is None or future.done():
                    continue
                db.execute("UPDATE calls SET status='released' WHERE call_id=?", (row["call_id"],))
                released.append(gate_id)
        for gate_id in released:
            self.gates[gate_id].set_result(outcome)
        return {"released_gate_ids": released}

    def continue_queue(self, request_id):
        body = {"action": "continue"}
        with self.repo.transaction() as (db, data):
            duplicate = self.repo.duplicate(db, request_id, body)
            if duplicate is not None:
                return duplicate
            data.update(queue_paused=False, queue_pause_reason=None)
            started = self.take_next(db, data)
            current = self.repo.run(data, data["current_run_id"])
            response = {
                "status": "running" if current and current["status"] == "running" else "idle",
                "started_run_id": started["run_id"] if started else None,
            }
            self.repo.remember(db, request_id, body, response)
        if started:
            self.spawn(started["run_id"], started["control_revision"], started["question"])
        return response

    def cancel(self, run_id, revision, request_id):
        body = {"action": "cancel", "run_id": run_id, "control_revision": revision}
        with self.repo.transaction() as (db, data):
            duplicate = self.repo.duplicate(db, request_id, body)
            if duplicate is not None:
                return duplicate
            if self.repo.run(data, run_id) is None:
                raise ApiError(404, "not_found", "실행이 없습니다.")
            run = self.repo.fenced(data, run_id, revision)
            if not run:
                raise ApiError(409, "revision_conflict", "현재 실행·버전이 아닙니다.")
            run.update(status="cancelled", phase="cancelled", updated_at=now())
            data.update(queue_paused=True, queue_pause_reason="cancelled")
            self.repo.save_revision(db, run)
            response = {"status": "cancelled"}
            self.repo.event(
                db,
                run_id,
                revision,
                "run.cancelled",
                "dispatcher",
                input=body,
                output=response,
                status="cancelled",
            )
            self.repo.remember(db, request_id, body, response)
        return response
