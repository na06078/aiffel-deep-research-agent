"""실제 ASGI/저장 경계의 순수 표현 조회. 모델·외부 네트워크는 명시적 대역이다."""

import hashlib

import pytest

from 테스트.테스트_보고서변환_API import completed
from 테스트.테스트_실행제어 import client_at

VERSION = "markdown-it4-playwright1248-svg-v2"
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 120">'
    '<title>합성 처리 흐름</title><rect x="10" y="10" width="280" height="90" '
    'fill="#edf1e5"/><text x="30" y="60" font-size="18">입력 → 결과</text></svg>'
)
ADDITION = (
    "\n\n## 합성 비교표\n\n| 항목 | 확인 결과 |\n|---|---|\n"
    "| 자료 | 검증 전용 합성 |\n\n### 합성 처리 흐름\n\n"
    "그림은 실행 경로를 시험하는 합성 도식입니다.\n\n```svg\n"
    + SVG
    + "\n```\n\n[공식 참고](https://example.org/reference)\n"
)


def file_hashes(path):
    return {
        p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in path.rglob("*")
        if p.is_file()
    }


def params(report, **overrides):
    return {
        "source_report_hash": report["sha256"],
        "renderer_version": VERSION,
        **overrides,
    }


async def visual_report(client, app):
    original = app.state.service.model.synthesize

    async def with_visuals(payload):
        return await original(payload) + ADDITION

    app.state.service.model.synthesize = with_visuals
    return await completed(client, app)


async def test_표현_GET은_같은_정본의_표와_SVG를_저장이나_추가호출없이_돌려준다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        report = await visual_report(client, app)
        # 성공 제어: 생성·필수 검사·정본 조회를 실제 API로 먼저 통과해야 한다.
        assert (await client.get(f"/api/spike/reports/{report['report_id']}")).status_code == 200
        before = file_hashes(tmp_path)
        snapshot_before = (await client.get("/api/spike/snapshot")).json()
        response = await client.get(
            f"/api/spike/reports/{report['report_id']}/presentation", params=params(report)
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["report_id"] == report["report_id"]
        assert result["source_report_hash"] == report["sha256"]
        assert result["renderer_version"] == VERSION
        assert "<table" in result["html"]
        assert "<svg" in result["html"]
        assert "입력 → 결과" in result["html"]
        assert result["warnings"] == []
        assert {"text": "공식 참고", "url": "https://example.org/reference"} in result["links"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert file_hashes(tmp_path) == before
        assert (await client.get("/api/spike/snapshot")).json() == snapshot_before
        assert not (tmp_path / "형식변환").exists()


@pytest.mark.parametrize(
    "overrides",
    [{"source_report_hash": "0" * 64}, {"renderer_version": "old-renderer"}],
)
async def test_표현_GET은_정본이나_렌더러가_다르면_정확한_오류로_거절한다(tmp_path, overrides):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        assert (await client.get(f"/api/spike/reports/{report['report_id']}")).status_code == 200
        before = file_hashes(tmp_path)
        response = await client.get(
            f"/api/spike/reports/{report['report_id']}/presentation",
            params=params(report, **overrides),
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "report_hash_mismatch"
        assert file_hashes(tmp_path) == before


async def test_삭제된_보고서의_표현을_조회해도_파일이나_기록을_살리지_않는다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        run_id = report["run_id"]
        deleted = await client.post(
            f"/api/spike/runs/{run_id}/delete",
            json={"confirm_run_id": run_id, "client_request_id": "presentation-delete"},
        )
        assert deleted.status_code == 200, deleted.text
        before = file_hashes(tmp_path)
        response = await client.get(
            f"/api/spike/reports/{report['report_id']}/presentation", params=params(report)
        )
        assert response.status_code == 404, response.text
        assert "error" in response.json()
        assert file_hashes(tmp_path) == before


async def test_표현_GET은_원본파일_변조를_렌더링하기_전에_검출한다(tmp_path):
    async with client_at(tmp_path) as (client, app):
        report = await completed(client, app)
        _, _, filename = app.state.service.report_bytes(report["report_id"])
        (tmp_path / filename).write_text("# 변조된 정본\n기존 해시와 다릅니다.", encoding="utf-8")
        before = file_hashes(tmp_path)
        response = await client.get(
            f"/api/spike/reports/{report['report_id']}/presentation", params=params(report)
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "report_integrity_error"
        assert file_hashes(tmp_path) == before
