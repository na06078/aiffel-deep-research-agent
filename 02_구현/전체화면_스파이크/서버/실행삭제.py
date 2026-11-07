"""실행 하나의 영구 삭제(명세 28.2~28.4): 미리보기와 삭제.

원칙 두 가지.
1. DB는 한 트랜잭션으로 지우고, 파일은 커밋한 뒤에 지운다. 파일 삭제는 되돌릴 수 없고
   DB 롤백과 묶을 수도 없으므로, DB가 확정된 다음에만 파일에 손을 댄다. 파일 단계가
   실패해도 이미 지운 DB는 되돌리지 않고 경고로만 알린다.
2. 호출 예산 장부(CallBudget)는 이 모듈이 알지 못한다. 지워진 실행의 비용 기록도
   누적 한도의 근거이므로 건드리지 않는다.
"""

import json
from pathlib import Path

from 서버.저장소 import ApiError, now

RUNNING_MESSAGE = "진행 중인 실행은 삭제할 수 없습니다. 먼저 취소하세요."
DEPENDENTS_MESSAGE = (
    "이 보고서를 문맥으로 쓴 실행이 있어 삭제할 수 없습니다. 먼저 그 실행을 삭제하세요."
)
WEB_DB = "웹자료/자료.sqlite3"
# 삭제 흔적 사건을 매다는 전역 가짜 run_id(기존 "selection"과 같은 방식).
TOMBSTONE_RUN = "deletions"


def _count(db, query, *args):
    return db.execute(query, args).fetchone()[0]


class RunDeletion:
    def __init__(self, service, exports):
        self.service = service
        self.exports = exports

    @property
    def repo(self):
        return self.service.repo

    # ------------------------------------------------------------ 조회(읽기 전용)

    def preview(self, run_id):
        """지울 범위와 막힘 사유를 계산한다. 어떤 것도 바꾸지 않는다."""
        with self.repo.read_connection() as db:
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            run = self.repo.run(state, run_id)
            if run is None:
                raise ApiError(404, "not_found", "실행이 없습니다.")
            reports = self._reports(db, run_id)
            counts = self._counts(db, run_id, reports)
        blockers = self._blockers(state, run, {r["report_id"] for r in reports})
        return {
            "run_id": run_id,
            "deletable": not blockers,
            "blockers": blockers,
            "counts": counts,
        }

    # ------------------------------------------------------------ 삭제

    def delete(self, run_id, confirm_run_id, client_request_id):
        body = {"action": "delete", "run_id": run_id}
        with self.repo.transaction() as (db, state):
            # 중복 확인이 404보다 먼저다: 이미 지워진 실행의 재전송도 저장된 응답을 받는다.
            duplicate = self.repo.duplicate(db, client_request_id, body)
            if duplicate is not None:
                return duplicate
            run = self.repo.run(state, run_id)
            if run is None:
                raise ApiError(404, "not_found", "실행이 없습니다.")
            if confirm_run_id != run_id:
                raise ApiError(422, "confirm_mismatch", "확인용 실행 ID가 경로의 ID와 다릅니다.")
            reports = self._reports(db, run_id)
            self._refuse_if_blocked(state, run, {r["report_id"] for r in reports})

            counts = self._counts(db, run_id, reports)
            self._delete_rows(db, run_id)
            self._drop_from_state(state, run_id)
            # 흔적은 사건 삭제 "뒤에" 넣는다. 먼저 넣으면 방금 지운 사건과 함께 사라지고,
            # 삭제한 실행이 가장 큰 seq였을 때 MAX(seq)가 줄어 화면이 갱신을 놓친다.
            # AUTOINCREMENT 덕분에 지금 넣는 행은 이전 최댓값보다 큰 seq를 받는다.
            self._leave_tombstone(db, run_id, counts)
            response = {
                "status": "deleted",
                "run_id": run_id,
                "counts": counts,
                "warnings": [],
                "current_run_id": state["current_run_id"],
            }
            # 삭제 요청 자신의 기록은 requests 정리 "뒤에" 남겨야 재전송이 응답을 찾는다.
            self.repo.remember(db, client_request_id, body, response)
        # 여기부터는 DB가 확정된 상태다. 파일 정리 실패는 경고로만 돌려준다.
        warnings = self._cleanup_files(run_id, reports)
        return {**response, "warnings": warnings}

    # ------------------------------------------------------------ 판정·집계

    @staticmethod
    def _reports(db, run_id):
        rows = db.execute(
            "SELECT report_id, filename FROM reports WHERE json_extract(body,'$.run_id')=?",
            (run_id,),
        ).fetchall()
        return [{"report_id": row[0], "filename": row[1]} for row in rows]

    def _counts(self, db, run_id, reports):
        ids = {r["report_id"] for r in reports}
        web = self._web_counts(run_id)
        return {
            "events": _count(
                db, "SELECT COUNT(*) FROM events WHERE json_extract(body,'$.run_id')=?", run_id
            ),
            "calls": _count(db, "SELECT COUNT(*) FROM calls WHERE run_id=?", run_id),
            "activity_spans": _count(
                db, "SELECT COUNT(*) FROM activity_spans WHERE run_id=?", run_id
            ),
            "requests": _count(
                db,
                "SELECT COUNT(*) FROM requests WHERE json_extract(body,'$.run_id')=?"
                " OR json_extract(response,'$.run_id')=?",
                run_id,
                run_id,
            ),
            "reports": len(reports),
            "report_files": sum(1 for r in reports if self._safe_report_path(r) is not None),
            "export_files": self.exports.count_files(ids),
            **web,
        }

    @staticmethod
    def _blockers(state, run, report_ids):
        blockers = []
        if run["status"] == "running":
            blockers.append({"code": "run_running", "message": RUNNING_MESSAGE})
        dependents = [
            {"run_id": r["run_id"], "question": r["question"], "created_at": r["created_at"]}
            for r in state["runs"]
            if r["run_id"] != run["run_id"] and r.get("parent_report_id") in report_ids
        ]
        if dependents:
            blockers.append(
                {"code": "has_dependents", "message": DEPENDENTS_MESSAGE, "dependents": dependents}
            )
        return blockers

    def _refuse_if_blocked(self, state, run, report_ids):
        # 검사 순서가 계약이다: 진행 중(409 run_running) 다음에 후속 실행(409 has_dependents).
        by_code = {b["code"]: b for b in self._blockers(state, run, report_ids)}
        for code in ("run_running", "has_dependents"):
            if code in by_code:
                raise ApiError(409, code, by_code[code]["message"])

    # ------------------------------------------------------------ DB 삭제(트랜잭션 안)

    @staticmethod
    def _delete_rows(db, run_id):
        db.execute("DELETE FROM revisions WHERE run_id=?", (run_id,))
        db.execute("DELETE FROM calls WHERE run_id=?", (run_id,))
        db.execute("DELETE FROM activity_spans WHERE run_id=?", (run_id,))
        db.execute("DELETE FROM events WHERE json_extract(body,'$.run_id')=?", (run_id,))
        db.execute(
            "DELETE FROM requests WHERE json_extract(body,'$.run_id')=?"
            " OR json_extract(response,'$.run_id')=?",
            (run_id, run_id),
        )
        db.execute("DELETE FROM reports WHERE json_extract(body,'$.run_id')=?", (run_id,))

    def _drop_from_state(self, state, run_id):
        state["runs"] = [r for r in state["runs"] if r["run_id"] != run_id]
        if state["current_run_id"] != run_id:
            return
        # 남은 실행 중 created_at이 가장 큰 것, 같으면 목록 뒤쪽. 없으면 None.
        latest = max(
            enumerate(state["runs"]),
            key=lambda item: (item[1]["created_at"], item[0]),
            default=None,
        )
        state["current_run_id"] = latest[1]["run_id"] if latest else None

    def _leave_tombstone(self, db, run_id, counts):
        # 지웠다는 사실과 개수만 남긴다. 질문·초안·보고서 내용은 넣지 않는다.
        self.repo.event(
            db,
            TOMBSTONE_RUN,
            0,
            "run.deleted",
            "dispatcher",
            input={"run_id": run_id},
            output={"run_id": run_id, "counts": counts, "deleted_at": now()},
            status="applied",
            summary="실행 삭제",
        )

    # ------------------------------------------------------------ 커밋 뒤 파일 정리

    def _safe_report_path(self, report):
        """저장소 폴더 안의 실제 존재하는 보고서 파일 경로. 밖이거나 없으면 None."""
        path = self._resolved_report_path(report)
        return path if path is not None and path.is_file() else None

    def _resolved_report_path(self, report):
        root = self.repo.root
        path = (root / (report["filename"] or "")).resolve()
        return path if path.is_relative_to(root) and path != root else None

    def _cleanup_files(self, run_id, reports):
        """각 단계가 서로 독립이다. 한 단계가 실패해도 다음 단계를 계속한다."""
        warnings = []
        for step in (
            lambda: self._delete_report_files(reports),
            lambda: self._purge_exports(reports),
            lambda: self._delete_web_rows(run_id),
        ):
            try:
                warnings.extend(step())
            except Exception:  # noqa: BLE001 -- 후속 단계 실패는 예외가 아니라 경고다.
                warnings.append("삭제 뒤 파일 정리 중 예기치 않은 오류가 있었습니다.")
        return warnings

    def _delete_report_files(self, reports):
        warnings = []
        for report in reports:
            path = self._resolved_report_path(report)
            if path is None:
                warnings.append(
                    f"저장소 밖을 가리키는 보고서 파일은 지우지 않았습니다: {report['filename']}"
                )
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                warnings.append(f"보고서 파일을 지우지 못했습니다: {path.name}")
        return warnings

    def _purge_exports(self, reports):
        return self.exports.purge({r["report_id"] for r in reports})["errors"]

    def _delete_web_rows(self, run_id):
        store = self._web_store()
        if store is None:
            return []
        try:
            store.delete_run(run_id)
        except Exception:  # noqa: BLE001 -- 웹 자료 DB 오류도 경고로 돌려준다.
            return ["웹 자료 기록을 지우지 못했습니다."]
        return []

    # ------------------------------------------------------------ 웹 자료 DB

    def _web_store(self):
        """웹 자료 DB가 이미 있을 때만 저장소를 돌려준다. 없으면 파일을 만들지 않는다.

        실제 서비스의 web_store는 지연 생성이라 접근만으로 DB를 만든다. 그래서 먼저
        경로를 확인하고, web_store가 없는 서비스(대역)는 건너뛴다.
        """
        if not hasattr(type(self.service), "web_store"):
            return None
        injected = getattr(self.service, "_web_store", None)
        path = injected.path if injected is not None else self.repo.root / WEB_DB
        return self.service.web_store if Path(path).is_file() else None

    def _web_counts(self, run_id):
        store = self._web_store()
        return store.count_run(run_id) if store else {"web_sources": 0, "web_reads": 0}
