"""교안 visited의 조사자-문서 귀속을 실제 호출 사건으로 검증한다."""

import json

import pytest

from 검증.교안비교_후처리 import reading_metrics


def test_후처리CLI는_저장파일을_읽기만하고_집계를출력한다(tmp_path, capsys):
    from 검증.교안비교_후처리 import main

    path = tmp_path / "아카이브.json"
    path.write_text(json.dumps({"run": {"control_revision": 1}, "events": []}), encoding="utf-8")
    before = path.read_bytes()
    assert main([str(path)]) == 0
    output = capsys.readouterr().out
    assert "읽기시도" in output
    assert json.loads(output)["중복률"] is None
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([("한명", f"문서{i}") for i in range(12)], (12, 12, 12, 0.0, 0.0)),
        (
            [("가", "문서1"), ("가", "문서2"), ("나", "문서1"), ("나", "문서3")],
            (4, 3, 4, 25.0, 25.0),
        ),
        ([("가", "문서1"), ("가", "문서1")], (2, 1, 1, 0.0, 50.0)),
    ],
)
def test_절별공유가아닌_실제조사자와문서쌍으로_중복을센다(pairs, expected):
    events = []
    for n, (actor, document) in enumerate(pairs):
        call_id = str(n)
        events.extend(
            [
                {
                    "type": "model.started",
                    "control_revision": 1,
                    "actor": actor,
                    "input": {
                        "context": {"operation": "read_one"},
                        "inputs": {"document": document},
                    },
                    "output": {"call_id": call_id},
                },
                {
                    "type": "model.completed",
                    "control_revision": 1,
                    "actor": actor,
                    "output": {"call_id": call_id, "content": "관측한 요약"},
                },
            ]
        )
    archive = {"run": {"control_revision": 1}, "events": events}
    actual = reading_metrics(archive)
    calls, unique, actor_pairs, duplicate, repeated = expected
    assert actual["읽기시도"] == actual["완료읽기"] == calls
    assert actual["고유문서"] == unique
    assert actual["조사자문서쌍"] == actor_pairs
    assert actual["중복률"] == duplicate
    assert actual["재읽기율"] == repeated


def test_미완료읽기는_완료로세지않고_구버전은제외한다():
    archive = {
        "run": {"control_revision": 2},
        "events": [
            {
                "type": "model.started",
                "control_revision": 1,
                "actor": "가",
                "input": {"context": {"operation": "read_one"}, "inputs": {"document": "이전"}},
                "output": {"call_id": "old"},
            },
            {
                "type": "model.completed",
                "control_revision": 1,
                "actor": "가",
                "output": {"call_id": "old", "content": "이전 응답"},
            },
            {
                "type": "model.started",
                "control_revision": 2,
                "actor": "가",
                "input": {"context": {"operation": "read_one"}, "inputs": {"document": "대기"}},
                "output": {"call_id": "waiting"},
            },
        ],
    }
    actual = reading_metrics(archive)
    assert actual["읽기시도"] == 1 and actual["완료읽기"] == 0
    assert actual["고유문서"] == 0 and actual["중복률"] is None
    assert actual["재읽기율"] is None
