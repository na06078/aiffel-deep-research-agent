"""구간 ID 순수 경계: 모델·제공자·파일 실행 없이 독립 기대값을 검사한다."""

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

from 서버.교안연구 import ResearchContractError
from 서버.보고서근거 import build_draft_request, materialize_section
from 서버.보고서품질 import citation_keys, validate_section, visible_markdown

ASSIGNMENT = {"role": "연표 담당", "title": "사건", "instruction": "연도를 설명"}
TEXT = "😀한글 사건은 1882년에 발생하였으며 기록에 남아 있다."


class SpanEvidenceTests(unittest.TestCase):
    def test_detached_strict_schema_and_schema_in_input_budget(self):
        sources = {"A": TEXT, "B": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        self.assertIsInstance(getattr(request, "response_schema", None), dict)
        schema = request.response_schema
        self.assertEqual(set(schema), {"type", "properties", "required", "additionalProperties"})
        self.assertEqual(schema["type"], "object")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(
            set(schema["required"]), {"근거목록ID", "문장", "근거관계", "충분", "부족"}
        )
        props = schema["properties"]
        self.assertEqual(
            props["근거목록ID"], {"type": "string", "enum": [request.inputs["evidence_set_id"]]}
        )
        self.assertEqual(props["충분"], {"type": "boolean"})
        self.assertEqual(props["부족"], {"type": "string"})
        self.assertEqual(props["문장"]["minItems"], 1)
        self.assertEqual(props["문장"]["maxItems"], 12)
        self.assertEqual(props["문장"]["items"], {"type": "string"})
        relations = props["근거관계"]
        self.assertEqual((relations["minItems"], relations["maxItems"]), (0, 16))
        item = relations["items"]
        self.assertIs(item["additionalProperties"], False)
        self.assertEqual(set(item["required"]), {"문장번호", "근거구간"})
        self.assertEqual(
            item["properties"],
            {
                "문장번호": {"type": "integer", "enum": list(range(1, 13))},
                "근거구간": {"type": "integer", "enum": [1, 2]},
            },
        )
        schema_bytes = len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(request.inputs["schema_bytes"], schema_bytes)
        self.assertEqual(request.inputs["response_schema"], schema)
        self.assertLessEqual(
            sum(len(p.encode()) for p in request.prompts) + schema_bytes + 8192, 272000
        )
        props["근거목록ID"]["enum"][0] = "tampered"
        item["properties"]["근거구간"]["enum"].append(99)
        snapshot = request.inputs
        snapshot["alias_map"]["1"] = "tampered"
        snapshot["private_catalog"][0][0] = "tampered"
        snapshot["response_schema"] = {}
        self.assertNotEqual(request.response_schema, schema)
        self.assertEqual(request.inputs["alias_map"]["1"], request._catalog[0][0])
        self.assertEqual(request.inputs["response_schema"], request.response_schema)

    def test_flat_single_claim_materialization(self):
        sources = {"A": TEXT, "B": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        raw = {
            "근거목록ID": request.inputs["evidence_set_id"],
            "문장": ["기록이 있다."],
            "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [1]],
            "충분": True,
            "부족": "",
        }
        try:
            result = materialize_section(json.dumps(raw), request, sources)
        except ResearchContractError as exc:
            self.fail(f"valid flat relation rejected: {exc}")
        self.assertEqual(result["근거"][0]["인용문"], TEXT)

    def test_catalog_token_binds_numeric_materialization_to_request(self):
        sources = {"A": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        payload = json.loads(request.prompts[1])
        self.assertIn("evidence_set_id", payload)
        raw = {
            "근거목록ID": payload["evidence_set_id"],
            "문장": ["기록이 있다."],
            "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [1]],
            "충분": True,
            "부족": "",
        }
        self.assertEqual(
            materialize_section(json.dumps(raw), request, sources)["근거"],
            [{"주장": "기록이 있다.", "출처": "A", "인용문": TEXT}],
        )
        for assignment, notes, changed in (
            ({**ASSIGNMENT, "title": "다른 절"}, [], sources),
            (ASSIGNMENT, [["A", "새 메모"]], sources),
            (ASSIGNMENT, [], {"A": TEXT + "변경"}),
            (ASSIGNMENT, [], {"B": TEXT}),
        ):
            other = build_draft_request(assignment, notes, changed)
            self.assertEqual(json.loads(other.prompts[1])["evidence"][0]["id"], 1)
            self.assert_rejected(json.dumps(raw), other, changed, "span response: evidence set")
        for ref in (True, False, "1", 1.0, request._catalog[0][0]):
            bad = {
                **raw,
                "문장": ["기록이 있다."],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [ref]],
            }
            self.assert_rejected(json.dumps(bad), request, sources, "span response: schema")
        for ref in (0, -1, 2):
            bad = {
                **raw,
                "문장": ["기록이 있다."],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [ref]],
            }
            self.assert_rejected(json.dumps(bad), request, sources, "span response: unoffered ID")
        self.assert_rejected(
            json.dumps({**raw, "근거목록ID": "wrong"}),
            request,
            sources,
            "span response: evidence set",
        )
        del raw["근거목록ID"]
        self.assert_rejected(json.dumps(raw), request, sources, "span response: schema")

    def test_short_numbers_are_request_local_not_private_hashes(self):
        request = build_draft_request(ASSIGNMENT, [], {"A": TEXT, "B": TEXT})
        payload = json.loads(request.prompts[1])
        self.assertEqual([s["id"] for s in payload["evidence"]], [1, 2])
        self.assertTrue(all(type(s["id"]) is int for s in payload["evidence"]))
        for span in request._catalog:
            self.assertRegex(span[0], r"^e[0-9a-f]{16}$")
        self.assertNotEqual(request._catalog[0][0], request._catalog[1][0])

    def test_only_plain_json_request_values_without_callbacks(self):
        calls = []

        class CallbackDict(dict):
            def items(self):
                calls.append("callback")
                return super().items()

        try:
            build_draft_request(ASSIGNMENT, [CallbackDict(secret="value")], {"A": TEXT})
        except ResearchContractError as exc:
            self.assertIn("span request: invalid inputs", str(exc))
        else:
            self.fail("non-plain JSON object accepted")
        self.assertEqual(calls, [])

    def test_only_string_response_accepted(self):
        request = build_draft_request(ASSIGNMENT, [], {"A": TEXT})
        ref = request.inputs["catalog"][0]["id"]
        raw = json.dumps(
            {
                "근거목록ID": request.inputs["evidence_set_id"],
                "문장": ["기록이 있다."],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [ref]],
                "충분": True,
                "부족": "",
            }
        )
        self.assert_rejected(raw.encode(), request, {"A": TEXT}, "span response: schema")

    def test_exact_prompt_byte_boundary_and_no_room_for_one_span(self):
        sources = {"A": TEXT}
        initial = build_draft_request(ASSIGNMENT, [["A", ""]], sources)
        size = sum(len(p.encode()) for p in initial.prompts) + initial.inputs["schema_bytes"] + 8192
        padding = "x" * (272000 - size)
        boundary = build_draft_request(ASSIGNMENT, [["A", padding]], sources)
        self.assertEqual(
            sum(len(p.encode()) for p in boundary.prompts) + boundary.inputs["schema_bytes"] + 8192,
            272000,
        )
        self.assertEqual(json.loads(boundary.prompts[1])["notes"], [["A", padding]])
        with self.assertRaisesRegex(ResearchContractError, "span request: input budget"):
            build_draft_request(ASSIGNMENT, [["A", padding + "x"]], sources)

    def test_pure_core_has_no_io_or_callback(self):
        # Warm only the same renderer import used by canonical validation.
        visible_markdown("가시 문장")
        with (
            patch("builtins.open") as builtin_open,
            patch("io.open") as io_open,
            patch("socket.socket") as network,
            patch("sqlite3.connect") as database,
            patch("subprocess.Popen") as process,
            patch("os.getenv") as getenv,
        ):
            for guard in (builtin_open, io_open, network, database, process, getenv):
                guard.side_effect = AssertionError("pure core performed I/O")
            request = build_draft_request(ASSIGNMENT, [], {"A": TEXT})
            ref = json.loads(request.prompts[1])["evidence"][0]["id"]
            raw = json.dumps(
                {
                    "근거목록ID": request.inputs["evidence_set_id"],
                    "문장": ["기록이 있다."],
                    "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [ref]],
                    "충분": True,
                    "부족": "",
                }
            )
            self.assertTrue(materialize_section(raw, request, {"A": TEXT})["충분"])
            for guard in (builtin_open, io_open, network, database, process, getenv):
                guard.assert_not_called()

    def test_span_tail_lengths_positions_versions_and_id_collision(self):
        for length in (10, 319, 320, 321, 329, 330, 640, 641):
            sources = {"A": "가" * length}
            request = build_draft_request(ASSIGNMENT, [], sources)
            spans = json.loads(request.prompts[1])["evidence"]
            self.assertEqual(
                "".join(s["text"] for s in sorted(spans, key=lambda s: s["start"])), sources["A"]
            )
            self.assertTrue(all(10 <= len(s["text"]) <= 320 for s in spans))
            self.assertEqual(len({s["id"] for s in spans}), len(spans))
            changed = build_draft_request(ASSIGNMENT, [], {"A": sources["A"] + "나"})
            self.assertTrue(
                {s[0] for s in request._catalog}.isdisjoint(s[0] for s in changed._catalog)
            )
        with patch("서버.보고서근거.hashlib.sha256") as hash_fn:
            hash_fn.return_value.hexdigest.return_value = "0" * 64
            with self.assertRaisesRegex(ResearchContractError, "span request: ID collision"):
                build_draft_request(ASSIGNMENT, [], {"A": TEXT, "B": TEXT})

    def test_genuinely_unoffered_same_version_id_and_unread_source(self):
        sources = {"A": TEXT * 1600 + ("유일단어의 " * 64)[:320]}
        request = build_draft_request(ASSIGNMENT, [], sources)
        other = build_draft_request(
            {**ASSIGNMENT, "title": "유일단어의", "instruction": "유일단어의"}, [], sources
        )
        offered = {s[0] for s in request._catalog}
        new_refs = {s[0] for s in other._catalog} - offered
        self.assertTrue(new_refs)
        for ref in new_refs:
            self.assert_rejected(
                json.dumps(
                    {
                        "근거목록ID": request.inputs["evidence_set_id"],
                        "문장": ["기록이 있다."],
                        "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [ref]],
                        "충분": True,
                        "부족": "",
                    }
                ),
                request,
                sources,
                "span response: schema",
            )
        read_request = build_draft_request(ASSIGNMENT, [], {"A": TEXT})
        foreign_request = build_draft_request(ASSIGNMENT, [], {"B": TEXT})
        foreign_ref = json.loads(foreign_request.prompts[1])["evidence"][0]["id"]
        raw = json.dumps(
            {
                "근거목록ID": foreign_request.inputs["evidence_set_id"],
                "문장": ["기록이 있다."],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [foreign_ref]],
                "충분": True,
                "부족": "",
            }
        )
        self.assert_rejected(raw, read_request, {"A": TEXT}, "span response: evidence set")

    def test_sources_version_key_and_slice_changes_are_rejected(self):
        sources = {"A": TEXT, "B": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        spans = json.loads(request.prompts[1])["evidence"]
        self.assertNotEqual(spans[0]["id"], spans[1]["id"])
        raw = json.dumps(
            {
                "문장": ["기록이 있다."],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [spans[0]["id"]]],
                "근거목록ID": request.inputs["evidence_set_id"],
                "충분": True,
                "부족": "",
            }
        )
        self.assertEqual(materialize_section(raw, request, sources)["근거"][0]["출처"], "A")
        for changed in (
            {"A": TEXT + "추가", "B": TEXT},
            {"A": TEXT.replace("1882", "1883"), "B": TEXT},
            {"A": TEXT},
            {"A": TEXT, "B": TEXT, "C": TEXT},
            {" A": TEXT, "B": TEXT},
        ):
            with self.subTest(changed=changed):
                self.assert_rejected(raw, request, changed, "span response: source version")
        span = request._catalog[0]
        for changed in (
            (span[0], span[1], 1, span[3], span[4]),
            (span[0], "B", span[2], span[3], span[4]),
            (*span[:4], "변조된 원문 구간입니다."),
        ):
            self.assert_rejected(
                raw, replace(request, _catalog=(changed,)), sources, "span response: span binding"
            )

    def test_inputs_mutation_cannot_change_private_source_pool(self):
        assignment = dict(ASSIGNMENT)
        notes = [["A", "원래 메모"]]
        sources = {"A": TEXT}
        request = build_draft_request(assignment, notes, sources)
        before = request.prompts
        snapshot = request.inputs
        snapshot["catalog"][0]["text"] = "변조"
        snapshot["catalog"][0]["source_id"] = "B"
        snapshot["source_hashes"]["A"] = "0" * 64
        snapshot["assignment"]["title"] = "변조"
        snapshot["notes"][0][1] = "변조"
        notes[0][1] = "외부 변경"
        assignment["title"] = "외부 변경"
        self.assertEqual(request.prompts, before)
        self.assertEqual(request.inputs["catalog"][0]["text"], TEXT)
        self.assertEqual(request.inputs["notes"], [["A", "원래 메모"]])
        with self.assertRaises(FrozenInstanceError):
            request.prompts = ("changed", "changed")
        raw = json.dumps(
            {
                "문장": ["기록이 있다."],
                "근거관계": [
                    {"문장번호": 1, "근거구간": ref} for ref in [request.inputs["catalog"][0]["id"]]
                ],
                "근거목록ID": request.inputs["evidence_set_id"],
                "충분": True,
                "부족": "",
            }
        )
        self.assertEqual(materialize_section(raw, request, sources)["근거"][0]["인용문"], TEXT)

    def test_citations_escape_once_without_identifier_whitespace_folding(self):
        source = r"자료&amp;_A\B  두칸"
        sources = {source: TEXT * 30}
        request = build_draft_request(ASSIGNMENT, [], sources)
        spans = json.loads(request.prompts[1])["evidence"]
        obj = {
            "근거목록ID": request.inputs["evidence_set_id"],
            "문장": [
                "**기록**은 [여기](https://invalid.test/비가시)에 있다.\n\n```text\n숨은 내용\n```"
            ],
            "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [s["id"] for s in spans[:2]]],
            "충분": True,
            "부족": "",
        }
        result = materialize_section(json.dumps(obj), request, sources)
        self.assertEqual(citation_keys(visible_markdown(result["본문"])), [source])
        self.assertEqual([e["출처"] for e in result["근거"]], [source, source])
        self.assertEqual(result["근거"][0]["주장"], "기록은 여기에 있다.")
        self.assertNotIn("비가시", result["근거"][0]["주장"])
        self.assertNotIn("숨은 내용", result["근거"][0]["주장"])
        # Canonical validation still rejects paraphrased source quotes.
        bad = {
            **result,
            "근거": [{**result["근거"][0], "인용문": "자료에 존재하지 않는 다른 인용입니다."}],
        }
        with self.assertRaisesRegex(ResearchContractError, "quote not in read source"):
            validate_section(json.dumps(bad), sources)

    def test_reference_total_16_not_17_and_chunks_12_not_13(self):
        sources = {"A": TEXT * 300}
        request = build_draft_request(ASSIGNMENT, [], sources)
        refs = [s["id"] for s in json.loads(request.prompts[1])["evidence"]]
        for count in (16, 17):
            obj = {
                "근거목록ID": request.inputs["evidence_set_id"],
                "문장": ["여러 기록이 있다."],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in refs[:count]],
                "충분": True,
                "부족": "",
            }
            if count == 16:
                self.assertEqual(
                    len(materialize_section(json.dumps(obj), request, sources)["근거"]), 16
                )
            else:
                self.assert_rejected(
                    json.dumps(obj), request, sources, "span response: reference limit"
                )
        for count in (12, 13):
            obj = {
                "근거목록ID": request.inputs["evidence_set_id"],
                "문장": [f"기록 {i}이 있다." for i in range(count)],
                "근거관계": [
                    {"문장번호": n + 1, "근거구간": ref}
                    for n, refs in enumerate([[refs[0]] for i in range(count)])
                    for ref in refs
                ],
                "충분": True,
                "부족": "",
            }
            if count == 12:
                self.assertEqual(
                    len(materialize_section(json.dumps(obj), request, sources)["근거"]), 12
                )
            else:
                self.assert_rejected(json.dumps(obj), request, sources, "span response: schema")

    def test_duplicate_refs_and_reserved_citations_are_rejected(self):
        sources = {"A": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        ref = json.loads(request.prompts[1])["evidence"][0]["id"]
        for content, refs, reason in (
            ("기록이 있다.", [ref, ref], "duplicate reference"),
            ("기록 «A»", [ref], "reserved citation"),
            ("기록 &#171;A&#187;", [ref], "reserved citation"),
        ):
            obj = {
                "근거목록ID": request.inputs["evidence_set_id"],
                "문장": [content],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in refs],
                "충분": True,
                "부족": "",
            }
            self.assert_rejected(json.dumps(obj), request, sources, "span response: " + reason)

    def test_hidden_links_fenced_code_and_blank_content_have_no_claim(self):
        sources = {"A": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        ref = json.loads(request.prompts[1])["evidence"][0]["id"]
        for content in (
            "",
            "  ",
            "```text\n숨은 주장\n```",
            "[숨은정의]: https://invalid.test/주장",
            "[](https://invalid.test/숨은주장)",
        ):
            obj = {
                "근거목록ID": request.inputs["evidence_set_id"],
                "문장": [content],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [ref]],
                "충분": True,
                "부족": "",
            }
            self.assert_rejected(
                json.dumps(obj), request, sources, "span response: visible content"
            )

    def test_insufficient_partial_draft_and_sufficiency_contradictions(self):
        sources = {"A": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        obj = {
            "근거목록ID": request.inputs["evidence_set_id"],
            "문장": ["추가 자료가 필요하다."],
            "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in []],
            "충분": False,
            "부족": "후속 기록 미확인",
        }
        result = materialize_section(json.dumps(obj), request, sources)
        self.assertFalse(result["충분"])
        self.assertEqual(result["근거"], [])
        for enough, reason in ((True, ""), (False, ""), (True, "부족함")):
            self.assert_rejected(
                json.dumps({**obj, "충분": enough, "부족": reason}),
                request,
                sources,
                "span response: sufficiency",
            )

    def test_protocol_token_and_scope_prompt(self):
        import hashlib

        import 서버.보고서근거 as core

        request = build_draft_request(ASSIGNMENT, [], {"A": TEXT})
        old_token = hashlib.sha256(
            json.dumps(
                [
                    ASSIGNMENT,
                    [],
                    sorted(request.inputs["source_hashes"].items()),
                    request.inputs["private_catalog"],
                ],
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        self.assertNotEqual(request.inputs["evidence_set_id"], old_token)
        self.assertEqual(getattr(core, "DRAFT_PROTOCOL", None), "short-number-flat-evidence-v2")
        self.assertEqual(request.inputs.get("draft_protocol"), "short-number-flat-evidence-v2")
        raw = {
            "근거목록ID": old_token,
            "문장": ["기록이 있다."],
            "근거관계": [{"문장번호": 1, "근거구간": 1}],
            "충분": True,
            "부족": "",
        }
        self.assert_rejected(json.dumps(raw), request, {"A": TEXT}, "span response: evidence set")
        prompt = request.prompts[0]
        for text in (
            "질문",
            "역할",
            "핵심 주장",
            "전체 연대기",
            "인과",
            "시점",
            "충돌",
            "600자",
            "2,000자",
            "1부터",
            "16개",
            "근거관계",
        ):
            self.assertIn(text, prompt)
        self.assertNotIn("6~12", prompt)
        self.assertNotIn("여섯 문장 이상", prompt)

    def test_noncontiguous_relations_group_by_sentence_and_preserve_pair_order(self):
        sources = {"A": "첫 자료에는 독립 기록이 있다.", "B": "두 번째 자료에도 별도 기록이 있다."}
        request = build_draft_request(ASSIGNMENT, [], sources)
        obj = {
            "근거목록ID": request.inputs["evidence_set_id"],
            "문장": ["**첫 주장**이다.", "둘째 주장이다.", "셋째 주장이다."],
            "근거관계": [
                {"문장번호": 3, "근거구간": 1},
                {"문장번호": 1, "근거구간": 2},
                {"문장번호": 2, "근거구간": 1},
                {"문장번호": 1, "근거구간": 1},
            ],
            "충분": True,
            "부족": "",
        }
        result = materialize_section(json.dumps(obj), request, sources)
        self.assertEqual(
            result["근거"],
            [
                {
                    "주장": "첫 주장이다.",
                    "출처": "B",
                    "인용문": "두 번째 자료에도 별도 기록이 있다.",
                },
                {"주장": "첫 주장이다.", "출처": "A", "인용문": "첫 자료에는 독립 기록이 있다."},
                {"주장": "둘째 주장이다.", "출처": "A", "인용문": "첫 자료에는 독립 기록이 있다."},
                {"주장": "셋째 주장이다.", "출처": "A", "인용문": "첫 자료에는 독립 기록이 있다."},
            ],
        )
        self.assertEqual(
            visible_markdown(result["본문"]),
            "첫 주장이다. «B» «A»\n둘째 주장이다. «A»\n셋째 주장이다. «A»",
        )

    def test_twelve_statements_sixteen_pairs_and_nine_nineteen_rejection(self):
        sources = {"A": TEXT, "B": TEXT, "C": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        obj = {
            "근거목록ID": request.inputs["evidence_set_id"],
            "문장": [f"주장 {i}." for i in range(1, 13)],
            "근거관계": [{"문장번호": i, "근거구간": 1} for i in range(1, 13)]
            + [{"문장번호": i, "근거구간": 2} for i in range(1, 5)],
            "충분": True,
            "부족": "",
        }
        result = materialize_section(json.dumps(obj), request, sources)
        self.assertEqual(len(result["근거"]), 16)
        self.assertEqual(
            [e["주장"] for e in result["근거"]],
            [
                "주장 1.",
                "주장 1.",
                "주장 2.",
                "주장 2.",
                "주장 3.",
                "주장 3.",
                "주장 4.",
                "주장 4.",
                "주장 5.",
                "주장 6.",
                "주장 7.",
                "주장 8.",
                "주장 9.",
                "주장 10.",
                "주장 11.",
                "주장 12.",
            ],
        )
        obj["문장"] = obj["문장"][:9]
        obj["근거관계"] = [
            {"문장번호": i, "근거구간": ref} for i in range(1, 10) for ref in (1, 2)
        ] + [{"문장번호": 1, "근거구간": 3}]
        with patch("서버.보고서근거.validate_section") as canonical:
            self.assert_rejected(
                json.dumps(obj), request, sources, "span response: reference limit"
            )
            canonical.assert_not_called()

    def test_relation_indices_exact_types_keys_and_actual_sentence_bounds(self):
        sources = {"A": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        good = {
            "근거목록ID": request.inputs["evidence_set_id"],
            "문장": ["기록이 있다."],
            "근거관계": [{"문장번호": 1, "근거구간": 1}],
            "충분": True,
            "부족": "",
        }
        self.assertTrue(materialize_section(json.dumps(good), request, sources)["충분"])
        for number in (0, -1, 2, 13):
            with self.subTest(number=number):
                bad = {**good, "근거관계": [{"문장번호": number, "근거구간": 1}]}
                self.assert_rejected(
                    json.dumps(bad), request, sources, "span response: statement index"
                )
        for relation in (
            None,
            [],
            {},
            {"문장번호": 1},
            {"근거구간": 1},
            {"문장번호": 1, "근거구간": 1, "주장": "다시 쓰기"},
            *({"문장번호": number, "근거구간": 1} for number in (True, False, "1", 1.0)),
            *({"문장번호": 1, "근거구간": ref} for ref in (True, False, "1", 1.0)),
        ):
            with self.subTest(relation=relation):
                self.assert_rejected(
                    json.dumps({**good, "근거관계": [relation]}),
                    request,
                    sources,
                    "span response: schema",
                )
        for relations in (None, {}, "1"):
            with self.subTest(relations=relations):
                self.assert_rejected(
                    json.dumps({**good, "근거관계": relations}),
                    request,
                    sources,
                    "span response: schema",
                )
        old = {key: value for key, value in good.items() if key != "근거관계"}
        old["문장"] = [{"내용": "기록이 있다.", "근거구간": [1]}]
        self.assert_rejected(json.dumps(old), request, sources, "span response: schema")
        self.assert_rejected(
            json.dumps({**good, "문장": old["문장"]}), request, sources, "span response: schema"
        )

    def test_partial_unbound_sentences_require_false_with_reason(self):
        sources = {"A": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        obj = {
            "근거목록ID": request.inputs["evidence_set_id"],
            "문장": ["기록이 있다.", "후속 자료는 미확인이다."],
            "근거관계": [{"문장번호": 1, "근거구간": 1}],
            "충분": False,
            "부족": "후속 자료 미확인",
        }
        result = materialize_section(json.dumps(obj), request, sources)
        self.assertEqual(result["근거"], [{"주장": "기록이 있다.", "출처": "A", "인용문": TEXT}])
        self.assertIn("후속 자료는 미확인이다.", visible_markdown(result["본문"]))
        self.assert_rejected(
            json.dumps({**obj, "충분": True, "부족": ""}),
            request,
            sources,
            "span response: sufficiency",
        )

    def assert_rejected(self, raw, request, sources, reason):
        with self.assertRaisesRegex(ResearchContractError, reason):
            materialize_section(raw, request, sources)

    def test_model_schema_rejects_unknown_legacy_and_wrong_types(self):
        sources = {"A": TEXT}
        request = build_draft_request(ASSIGNMENT, [], sources)
        ref = json.loads(request.prompts[1])["evidence"][0]["id"]
        good = {
            "근거목록ID": request.inputs["evidence_set_id"],
            "문장": ["기록이 존재한다."],
            "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [ref]],
            "충분": True,
            "부족": "",
        }
        bad = [
            {key: value for key, value in good.items() if key != "근거목록ID"},
            {**good, "근거목록ID": 1},
            {**good, "근거목록ID": None},
            {**good, "근거": []},
            {"본문": "기록", "근거": [], "충분": True, "부족": ""},
            {**good, "충분": 1},
            {**good, "부족": None},
            {**good, "문장": []},
            {**good, "문장": {}},
            {**good, "문장": [None]},
            {**good, "문장": [{"내용": 1, "근거구간": [ref]}]},
            {**good, "문장": [{"내용": "기록", "근거구간": [ref], "인용문": TEXT}]},
            {**good, "문장": [{"내용": "기록", "근거구간": ref}]},
            {**good, "문장": [{"내용": "기록", "근거구간": ["1"]}]},
        ]
        self.assertTrue(materialize_section(json.dumps(good), request, sources)["충분"])
        for obj in bad:
            with self.subTest(obj=obj):
                self.assert_rejected(json.dumps(obj), request, sources, "span response: schema")
        for raw in ("not json", "[]", '{"문장":[],"문장":[],"충분":false,"부족":"내용"}'):
            with self.subTest(raw=raw):
                self.assert_rejected(raw, request, sources, "span response: schema")

    def test_unknown_foreign_unoffered_and_noncanonical_ids_rejected(self):
        sources = {"A": TEXT * 1600}
        request = build_draft_request(ASSIGNMENT, [], sources)
        offered = json.loads(request.prompts[1])["evidence"]
        other = build_draft_request(ASSIGNMENT, [], {"B": TEXT})
        unoffered = build_draft_request(
            {**ASSIGNMENT, "title": "유일단어", "instruction": "유일단어"},
            [],
            {"A": TEXT * 1600 + "유일단어가 존재하는 마지막 원문입니다."},
        )
        for foreign in (other, unoffered):
            raw = json.dumps(
                {
                    "근거목록ID": foreign.inputs["evidence_set_id"],
                    "문장": ["기록이 있다."],
                    "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [1]],
                    "충분": True,
                    "부족": "",
                }
            )
            self.assert_rejected(raw, request, sources, "span response: evidence set")
        for ref, reason in (
            ("e0000000000000000", "schema"),
            (request._catalog[0][0].upper(), "schema"),
            (" 1", "schema"),
            (len(offered) + 1, "unoffered ID"),
        ):
            raw = json.dumps(
                {
                    "근거목록ID": request.inputs["evidence_set_id"],
                    "문장": ["기록이 있다."],
                    "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [ref]],
                    "충분": True,
                    "부족": "",
                }
            )
            with self.subTest(ref=ref):
                self.assert_rejected(raw, request, sources, "span response: " + reason)

    def test_budget_trims_only_catalog_and_rejects_impossible_base(self):
        sources = {f"자료{i}": "배경 자료와 사건의 기록입니다. " * 3000 for i in range(6)}
        notes = [["메모", "가" * 80000]]
        request = build_draft_request(ASSIGNMENT, notes, sources)
        self.assertLessEqual(sum(len(p.encode()) for p in request.prompts) + 8192, 272000)
        payload = json.loads(request.prompts[1])
        self.assertEqual(payload["notes"], notes)
        self.assertEqual(request.inputs["source_count"], 6)
        self.assertTrue(payload["evidence"])
        self.assertLess(request.inputs["selected_count"], 128)
        numbers = [span["id"] for span in payload["evidence"]]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        props = request.response_schema["properties"]
        self.assertEqual(props["근거목록ID"]["enum"], [payload["evidence_set_id"]])
        self.assertEqual(props["근거관계"]["items"]["properties"]["근거구간"]["enum"], numbers)
        self.assertEqual(request.inputs["evidence_set_id"], payload["evidence_set_id"])
        self.assertEqual(len(request.inputs["alias_map"]), len(numbers))
        self.assertEqual(len(request.inputs["private_catalog"]), len(numbers))
        self.assertLessEqual(
            sum(len(p.encode()) for p in request.prompts)
            + len(json.dumps(request.response_schema, ensure_ascii=False).encode())
            + 8192,
            272000,
        )
        full = build_draft_request(ASSIGNMENT, [], sources)
        pruned = request.inputs["private_catalog"]
        self.assertEqual(pruned, full.inputs["private_catalog"][: len(pruned)])
        for number, span in zip(numbers, pruned):
            self.assertEqual(request.inputs["alias_map"][str(number)], span[0])
            raw = {
                "근거목록ID": payload["evidence_set_id"],
                "문장": ["기록이 있다."],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [number]],
                "충분": True,
                "부족": "",
            }
            entry = materialize_section(json.dumps(raw), request, sources)["근거"][0]
            self.assertEqual((entry["출처"], entry["인용문"]), (span[1], span[4]))
        with self.assertRaisesRegex(ResearchContractError, "span request: input budget"):
            build_draft_request(ASSIGNMENT, [["메모", "가" * 100000]], sources)

    def test_request_rejects_invalid_inputs_and_no_valid_span(self):
        for sources in ({}, {"짧은자료": "123456789"}, {"공백": " " * 100}):
            with (
                self.subTest(sources=sources),
                self.assertRaisesRegex(ResearchContractError, "span request: no valid evidence"),
            ):
                build_draft_request(ASSIGNMENT, [], sources)
        for assignment, notes, sources in (
            ([], [], {"A": TEXT}),
            (ASSIGNMENT, {}, {"A": TEXT}),
            (ASSIGNMENT, [], {1: TEXT}),
            (ASSIGNMENT, [], {"A": 3}),
            (ASSIGNMENT, [float("nan")], {"A": TEXT}),
        ):
            with (
                self.subTest(assignment=assignment, notes=notes, sources=sources),
                self.assertRaisesRegex(ResearchContractError, "span request: invalid inputs"),
            ):
                build_draft_request(assignment, notes, sources)

    def test_large_six_source_catalog_is_bounded_relevant_and_versioned(self):
        # Six documents exceed the actual three-document byte pressure case.
        sources = {
            f"자료{i}": ("배경 설명 내용입니다. " * 4000) + "사건 연도 특별핵심 1882년 자료입니다."
            for i in range(6)
        }
        request = build_draft_request(ASSIGNMENT, [["자료0", "특별핵심"]], sources)
        payload = json.loads(request.prompts[1])
        catalog = payload["evidence"]
        self.assertLessEqual(len(catalog), 128)
        self.assertLessEqual(len(json.dumps(catalog, ensure_ascii=False).encode()), 96000)
        self.assertEqual({s["source_id"] for s in catalog}, set(sources))
        self.assertIn("특별핵심", catalog[0]["text"])
        self.assertGreater(catalog[0]["start"], 20000)
        self.assertEqual(len({s["id"] for s in catalog}), len(catalog))
        for span in catalog:
            self.assertTrue(10 <= len(span["text"]) <= 320)
            self.assertEqual(span["text"], sources[span["source_id"]][span["start"] : span["end"]])
        self.assertEqual(
            json.loads(
                build_draft_request(
                    ASSIGNMENT, [["자료0", "특별핵심"]], dict(reversed(list(sources.items())))
                ).prompts[1]
            ),
            payload,
        )
        self.assertEqual(request.inputs["source_hashes"].keys(), sources.keys())
        self.assertEqual(request.inputs["catalog"], catalog)
        self.assertEqual(request.inputs["selected_count"], len(catalog))
        self.assertEqual(request.inputs["source_count"], 6)
        self.assertEqual(
            request.inputs["prompt_bytes"], sum(len(p.encode()) for p in request.prompts)
        )
        self.assertLessEqual(request.inputs["prompt_bytes"] + 8192, 272000)

    def test_exact_unicode_slice_request_and_canonical_materialization(self):
        sources = {"자료A": TEXT}
        request = build_draft_request(ASSIGNMENT, [["자료A", "연도 메모"]], sources)
        payload = json.loads(request.prompts[1])
        self.assertEqual(set(payload), {"assignment", "notes", "evidence_set_id", "evidence"})
        span = payload["evidence"][0]
        self.assertEqual(set(span), {"id", "source_id", "start", "end", "text"})
        self.assertEqual((span["start"], span["end"], span["text"]), (0, len(TEXT), TEXT))
        self.assertEqual(span["id"], 1)
        raw = json.dumps(
            {
                "문장": ["**사건**은 기록되었다."],
                "근거관계": [{"문장번호": 1, "근거구간": ref} for ref in [span["id"]]],
                "근거목록ID": request.inputs["evidence_set_id"],
                "충분": True,
                "부족": "",
            }
        )
        result = materialize_section(raw, request, sources)
        self.assertEqual(
            result["근거"], [{"주장": "사건은 기록되었다.", "출처": "자료A", "인용문": TEXT}]
        )
        self.assertIn("«자료A»", result["본문"])
        self.assertEqual(set(result), {"본문", "충분", "부족", "근거"})


if __name__ == "__main__":
    unittest.main()
