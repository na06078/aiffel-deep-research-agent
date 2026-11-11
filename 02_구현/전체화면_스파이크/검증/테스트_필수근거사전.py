"""최신 작성 응답 스키마가 문장별 필수 근거를 강제하는지 확인한다."""

import copy
import importlib
from pathlib import Path

import pytest

I = Path(__file__).resolve().parents[1]


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.syspath_prepend(str(I / "검증"))
    return importlib.import_module("보고서품질_실제연구검사")


def schema_case():
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["근거목록ID", "문장", "충분", "부족"],
        "properties": {
            "근거목록ID": {"type": "string", "enum": ["fixed-request"]},
            "문장": {
                "type": "array",
                "minItems": 0,
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["내용", "근거구간"],
                    "properties": {
                        "내용": {"type": ["string", "null"]},
                        "근거구간": {"type": "integer", "enum": [1, 2]},
                    },
                },
            },
            "충분": {"type": "boolean"},
            "부족": {"type": "string"},
        },
    }, {"evidence_set_id": "fixed-request", "evidence": [{"id": 1}, {"id": 2}]}


def test_문장과필수근거가_묶인스키마는_허용한다(driver):
    schema, offered = schema_case()
    original = copy.deepcopy(schema)
    driver.validate_draft_schema(schema, offered)
    assert schema == original


@pytest.mark.parametrize(
    "mutation",
    [
        "flat",
        "optional_ref",
        "empty_ref",
        "unknown_ref",
        "foreign_set",
        "extra_field",
        "cap17",
        "enum_bool",
        "no_support",
    ],
)
def test_필수근거를_보장하지않는_스키마는_거절한다(driver, mutation):
    schema, offered = schema_case()
    item = schema["properties"]["문장"]["items"]
    if mutation == "flat":
        schema["properties"]["문장"]["items"] = {"type": "string"}
    elif mutation == "optional_ref":
        item["required"] = ["내용"]
    elif mutation == "empty_ref":
        item["properties"]["근거구간"] = {"type": "array", "minItems": 0}
    elif mutation == "unknown_ref":
        item["properties"]["근거구간"]["enum"] = [1, 2, 3]
    elif mutation == "foreign_set":
        schema["properties"]["근거목록ID"]["enum"] = ["other-request"]
    elif mutation == "cap17":
        schema["properties"]["문장"]["maxItems"] = 17
    elif mutation == "enum_bool":
        item["properties"]["근거구간"]["enum"] = [True, 2]
    elif mutation == "no_support":
        item["properties"]["내용"]["type"] = "string"
    else:
        item["additionalProperties"] = True
    with pytest.raises(AssertionError):
        driver.validate_draft_schema(schema, offered)
