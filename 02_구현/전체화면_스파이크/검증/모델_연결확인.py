"""승인된 실제 모델 호환성 1회. 연구와 같은 영속 장부를 사용한다."""

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1]
ROOT = SPIKE.parents[1]
sys.path.insert(0, str(SPIKE))

from 서버.실제모델 import ModelCallError, OpenAIModel


async def main():
    output = SPIKE / "검증결과/실제연구_연결확인.json"
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous.get("status") == "PASS":
            print("COMPATIBILITY_ALREADY_VERIFIED; no additional call", flush=True)
            return 0
    result = {"checked_at": datetime.now(UTC).isoformat(), "scope": "실제 모델 최소 호환성 1회"}
    model = None
    code = 1
    try:
        model = OpenAIModel(ROOT / ".env", ROOT / "03_실행데이터/실제연구/호출예산.sqlite3")
        before = model.usage()
        response = await model.complete(
            'Return only the JSON object {"ok":true}.',
            "Respond with the requested JSON object. This is an API compatibility check.",
            context={
                "run_id": "compatibility",
                "revision": 1,
                "actor": "compatibility",
                "operation": "compatibility",
            },
            max_output_tokens=512,
            json_mode=True,
        )
        assert json.loads(response["content"]) == {"ok": True}
        result.update(status="PASS", response=response, before=before, after=model.usage())
        code = 0
    except ModelCallError as error:
        result.update(
            status="FAIL",
            code=error.code,
            http_status=error.status,
            attempt_id=error.attempt_id,
            message=error.safe_message,
        )
        if model:
            result["after"] = model.usage()
    except Exception:  # noqa: BLE001 -- 검증 오류 원문에 입력을 노출하지 않는다.
        result.update(
            status="FAIL",
            code="compatibility_validation_failed",
            message="응답의 호환성 기준을 확인하지 못했습니다.",
        )
    finally:
        if model:
            await model.aclose()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
