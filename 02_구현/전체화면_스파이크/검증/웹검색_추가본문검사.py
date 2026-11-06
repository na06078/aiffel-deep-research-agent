"""저장한 무료 대안 검색 결과에서 남은 본문만 수집한다. 모델/검색 재호출 없음."""

import hashlib
import importlib.util
import json
from pathlib import Path

r = Path.cwd()
e = r / "02_구현/전체화면_스파이크/검증결과"
source = r / "02_구현/전체화면_스파이크/검증/웹검색_무료연결검사.py"
spec = importlib.util.spec_from_file_location("probe", source)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
a = json.loads((e / "웹검색_무료연결검증.json").read_text(encoding="utf-8"))
b = json.loads((e / "웹검색_무료대안검증.json").read_text(encoding="utf-8"))
assert a["page_count"] == 1 and b["successful_queries"] == 4
wanted = {
    "https://docs.python.org/ko/3/library/asyncio-task.html",
    "https://www.data.go.kr/data/15077093/openapi.do",
    "https://docs.python.org/3/library/asyncio.html",
    "https://docs.langchain.com/oss/python/langgraph/overview",
    "https://reference.langchain.com/python/langgraph/types/Send",
}
remaining = 6 - a["page_count"]
assert len(wanted) == remaining
out = r / ".cache/웹검색_본문추가"
out.mkdir(parents=True, exist_ok=True)
input_path = out / "검색기록.jsonl"
assert not input_path.exists()
selected = []
for q in b["queries"]:
    kept = [x for x in q["results"] if x["href"] in wanted]
    selected.extend(x["href"] for x in kept)
    probe.append(
        input_path,
        {
            **q,
            "results": kept,
            "selection_only": True,
            "original_full_results": "검증결과/웹검색_무료대안검증.json",
        },
    )
assert set(selected) == wanted and len(selected) == len(wanted)
probe.OUTPUT = out
probe.PUBLIC_HOSTS.add("reference.langchain.com")
before = probe.ledger_digest()
probe.fetch_phase()
pages = probe.records(out / "본문기록.jsonl")
assert len(pages) == remaining and probe.ledger_digest() == before
all_pages = a["pages"] + pages
queries = b["queries"]
assert len(all_pages) <= 6
success = sum(x["status"] == "ok" for x in all_pages)
proof = {
    "status": "PASS_LIMITED" if success >= 3 and b["successful_queries"] == 4 else "PARTIAL",
    "scope": "무료 DDGS Yahoo의같은4질문+본문총최대6페이지; 일반웹신뢰성/지속무제한보장아님",
    "first_trial": {
        "reference": "웹검색_무료연결검증.json",
        "status": a["status"],
        "successful_queries": a["query_success"],
    },
    "alternative": {
        "reference": "웹검색_무료대안검증.json",
        "backend": "yahoo",
        "status": b["status"],
        "successful_queries": b["successful_queries"],
    },
    "unique_query_count": len({x["query"] for x in a["queries"] + queries}),
    "logical_search_attempts": len(a["queries"]) + len(queries),
    "observed_library_requests": sum(len(x["requests"]) for x in a["queries"] + queries),
    "fetched_page_attempts": len(all_pages),
    "successful_pages": success,
    "additional_pages": pages,
    "initial_page_reference": "웹검색_무료연결검증.json",
    "date_policy": "추출 날짜는 후보로 보존하고 확인하지 못한 발행일은null",
    "new_model_requests": 0,
    "paid_search_api_requests": 0,
    "model_ledger_unchanged": True,
    "measured_at": probe.now(),
    "probe_script_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
}
(e / "웹검색_무료연결_최종검증.json").write_text(
    json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(
    json.dumps(
        {k: v for k, v in proof.items() if k != "additional_pages"}, ensure_ascii=False, indent=2
    )
)
