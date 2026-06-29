"""LangSmith evaluator unit tests."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from scripts.langsmith_evaluators import reference_overlap


class LangsmithEvaluatorTests(unittest.TestCase):
    def test_reference_overlap_substring(self) -> None:
        run = SimpleNamespace(outputs={"answer": "梅西在世界杯共打进13球（5届世界杯，26次出场）。"})
        example = SimpleNamespace(outputs={"reference": "梅西在世界杯共打进13球"})
        result = reference_overlap(run, example)
        self.assertEqual(result["score"], 1.0)

    def test_reference_overlap_digits(self) -> None:
        run = SimpleNamespace(outputs={"answer": "意大利4次冠军：1934、1938、1982、2006"})
        example = SimpleNamespace(outputs={"reference": "意大利共4次世界杯冠军：1934、1938、1982、2006。"})
        result = reference_overlap(run, example)
        self.assertEqual(result["score"], 1.0)

    def test_reference_overlap_no_match(self) -> None:
        run = SimpleNamespace(outputs={"answer": "不知道"})
        example = SimpleNamespace(outputs={"reference": "梅西在世界杯共打进13球。"})
        result = reference_overlap(run, example)
        self.assertEqual(result["score"], 0.0)

    def test_reference_overlap_partial_fragment_fails(self) -> None:
        run = SimpleNamespace(outputs={"answer": "梅西"})
        example = SimpleNamespace(outputs={"reference": "梅西在世界杯共打进13球（5届世界杯，26次出场）。"})
        result = reference_overlap(run, example)
        self.assertEqual(result["score"], 0.0)

    def test_reference_overlap_digit_not_substring_of_other_digits(self) -> None:
        run = SimpleNamespace(outputs={"answer": "意大利14次冠军"})
        example = SimpleNamespace(outputs={"reference": "意大利共4次世界杯冠军。"})
        result = reference_overlap(run, example)
        self.assertEqual(result["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
