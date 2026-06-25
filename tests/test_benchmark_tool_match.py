"""Unit tests for benchmark tool matching helpers."""

from __future__ import annotations

import unittest

from benchmark.benchmark import case_expects_sql, match_expected_tool


class BenchmarkToolMatchTests(unittest.TestCase):
    def test_single_expected_tool(self):
        case = {"expected_tool": "player_stats"}
        self.assertTrue(match_expected_tool(case, ["player_stats"]))
        self.assertFalse(match_expected_tool(case, ["sql_query"]))

    def test_expected_tools_any(self):
        case = {"expected_tools": ["semantic_search", "sql_query"]}
        self.assertTrue(match_expected_tool(case, ["sql_query"]))
        self.assertTrue(match_expected_tool(case, ["semantic_search"]))
        self.assertFalse(match_expected_tool(case, ["player_stats"]))

    def test_case_expects_sql_from_list(self):
        case = {"expected_tools": ["semantic_search", "sql_query"]}
        self.assertTrue(case_expects_sql(case))


if __name__ == "__main__":
    unittest.main()
