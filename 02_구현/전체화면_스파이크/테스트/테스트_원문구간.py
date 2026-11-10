"""독립 합성 원문으로 연속 위치·문장 경계·하드 상한을 검사한다."""

import unittest
from unittest.mock import patch

from 서버.원문구간 import source_ranges


class SourceRangeTests(unittest.TestCase):
    def assert_partition(self, text):
        ranges = source_ranges(text)
        if len(text) < 10:
            self.assertEqual(ranges, [])
            return
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], len(text))
        self.assertTrue(all(10 <= end - start <= 400 for start, end in ranges))
        self.assertEqual([b for _, b in ranges[:-1]], [a for a, _ in ranges[1:]])
        self.assertEqual("".join(text[a:b] for a, b in ranges), text)
        self.assertEqual(source_ranges(text), ranges)

    def test_sentence_and_paragraph_literal_offsets(self):
        sentence = "가" * 298 + ". "
        paragraph = "나" * 298 + "\r\n"
        tail = "다" * 100
        self.assertEqual(source_ranges(sentence + tail), [(0, 300), (300, 400)])
        self.assertEqual(source_ranges(paragraph + tail), [(0, 300), (300, 400)])

    def test_complete_sentence_can_exceed_target_but_not_hard_limit(self):
        text = "가" * 349 + ". " + "나" * 100
        self.assertEqual(source_ranges(text), [(0, 351), (351, 451)])
        self.assertEqual(source_ranges("가" * 399 + "."), [(0, 400)])
        self.assertEqual(source_ranges("가" * 400 + "."), [(0, 320), (320, 401)])

    def test_unpunctuated_and_long_sentence_continuous_fallback(self):
        self.assertEqual(
            source_ranges("가" * 1000), [(0, 320), (320, 640), (640, 960), (960, 1000)]
        )
        self.assertEqual(source_ranges("가" * 640 + "."), [(0, 320), (320, 641)])
        for size in range(1301):
            with self.subTest(size=size):
                self.assert_partition("가" * size)

    def test_unicode_whitespace_quotes_and_newlines_are_exact(self):
        samples = [
            "  😀e\u0301한글\t문장이다.”\r\n\r\n" * 70,
            "甲" * 297 + "！？ ”\n" + "나" * 111,
            " \r\n\t" * 300,
            "앞 문단\n" * 100 + "마지막 꼬리",
            "가" * 310 + ". " + "나" * 9,
            "가" * 399 + ". " + "나" * 9,
        ]
        for text in samples:
            with self.subTest(text=text[:30]):
                self.assert_partition(text)
        text = "甲" * 297 + "。”\n" + "나" * 100
        self.assertEqual(source_ranges(text), [(0, 300), (300, 400)])

    def test_pure_stdlib_no_io(self):
        with (
            patch("builtins.open", side_effect=AssertionError("file I/O")) as file,
            patch("socket.socket", side_effect=AssertionError("network I/O")) as network,
            patch("os.getenv", side_effect=AssertionError("environment I/O")) as env,
        ):
            self.assertEqual(source_ranges("가" * 10), [(0, 10)])
            file.assert_not_called()
            network.assert_not_called()
            env.assert_not_called()


if __name__ == "__main__":
    unittest.main()
