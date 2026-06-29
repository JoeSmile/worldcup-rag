"""Prompt structure and few-shot section."""

from __future__ import annotations

import unittest

from prompts import (
    CONSTRAINTS,
    FEW_SHOT_EXAMPLES,
    ROLE,
    SYSTEM_PROMPT,
    build_system_prompt,
)


class PromptStructureTests(unittest.TestCase):
    def test_system_prompt_contains_structured_sections(self) -> None:
        for tag in ("<role>", "<context>", "<constraints>", "<tools>", "<few_shot_examples>"):
            self.assertIn(tag, SYSTEM_PROMPT)

    def test_few_shot_covers_tool_patterns(self) -> None:
        self.assertIn("player_stats", FEW_SHOT_EXAMPLES)
        self.assertIn("semantic_search", FEW_SHOT_EXAMPLES)
        self.assertIn("sql_query", FEW_SHOT_EXAMPLES)
        self.assertIn("search_players", FEW_SHOT_EXAMPLES)

    def test_build_system_prompt_matches_constant(self) -> None:
        self.assertEqual(build_system_prompt(), SYSTEM_PROMPT)

    def test_security_in_constraints(self) -> None:
        self.assertIn("提示词注入", CONSTRAINTS)
        self.assertIn("<role>", ROLE)


if __name__ == "__main__":
    unittest.main()
