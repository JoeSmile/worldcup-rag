"""Complex flow multi-step SQL planning and replan loop."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from workflows.complex_flow import (
    _heuristic_sql_plans,
    step_replan_execute_loop,
)
from workflows.complex_flow_llm import (
    MAX_REPLAN_ROUNDS,
    MAX_SQL_STEPS,
    _extract_json_object,
    _normalize_replan,
)
from workflows.base import WorkflowContext


class ComplexFlowPlannerTests(unittest.TestCase):
    def test_extract_json_from_markdown_fence(self) -> None:
        text = '```json\n{"done":false,"reason":"test","step":{"purpose":"a","sql":"SELECT 1"}}\n```'
        payload = _extract_json_object(text)
        self.assertIsNotNone(payload)
        step, reason, done, explicit_done = _normalize_replan(payload or {})
        self.assertEqual(reason, "test")
        self.assertFalse(done)
        self.assertFalse(explicit_done)
        self.assertIsNotNone(step)
        self.assertTrue(step["sql"].upper().startswith("SELECT"))

    def test_normalize_replan_rejects_unsafe_sql(self) -> None:
        step, reason, done, explicit_done = _normalize_replan(
            {
                "done": False,
                "reason": "bad",
                "step": {"purpose": "x", "sql": "DELETE FROM vw_player_summary"},
            }
        )
        self.assertTrue(done)
        self.assertFalse(explicit_done)
        self.assertIsNone(step)

    def test_normalize_replan_string_false_is_not_done(self) -> None:
        step, reason, done, explicit_done = _normalize_replan(
            {
                "done": "false",
                "reason": "继续",
                "step": {
                    "purpose": "next",
                    "sql": "SELECT 1 AS n",
                },
            }
        )
        self.assertFalse(done)
        self.assertFalse(explicit_done)
        self.assertIsNotNone(step)
        self.assertEqual(reason, "继续")

    def test_normalize_replan_string_true_is_done(self) -> None:
        step, reason, done, explicit_done = _normalize_replan(
            {"done": "true", "reason": "够了", "step": None}
        )
        self.assertTrue(done)
        self.assertTrue(explicit_done)
        self.assertIsNone(step)

    def test_normalize_replan_done(self) -> None:
        step, reason, done, explicit_done = _normalize_replan(
            {"done": True, "reason": "够了", "step": None}
        )
        self.assertTrue(done)
        self.assertTrue(explicit_done)
        self.assertIsNone(step)
        self.assertEqual(reason, "够了")

    def test_normalize_replan_next_step(self) -> None:
        step, reason, done, explicit_done = _normalize_replan(
            {
                "done": False,
                "reason": "查C罗",
                "step": {
                    "purpose": "C罗",
                    "sql": "SELECT display_name, goals FROM vw_player_summary LIMIT 1",
                },
            }
        )
        self.assertFalse(done)
        self.assertFalse(explicit_done)
        self.assertIsNotNone(step)
        self.assertIn("vw_player_summary", step["sql"])

    def test_heuristic_compare_players(self) -> None:
        plans = _heuristic_sql_plans("梅西和C罗谁世界杯进球更多？")
        self.assertGreaterEqual(len(plans), 2)
        self.assertTrue(all("vw_player_summary" in p["sql"] for p in plans))

    def test_replan_loop_executes_two_steps(self) -> None:
        ctx = WorkflowContext(query="梅西和C罗谁世界杯进球更多？")

        def fake_replan(query, executed_steps, **kwargs):
            if not executed_steps:
                return (
                    {
                        "purpose": "梅西",
                        "sql": "SELECT display_name, goals FROM vw_player_summary WHERE display_name ILIKE '%Messi%' LIMIT 1",
                    },
                    "先梅西",
                    False,
                    {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
                    "llm",
                )
            if len(executed_steps) == 1:
                return (
                    {
                        "purpose": "C罗",
                        "sql": "SELECT display_name, goals FROM vw_player_summary WHERE display_name ILIKE '%Ronaldo%' LIMIT 1",
                    },
                    "再C罗",
                    False,
                    {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
                    "llm",
                )
            return None, "完成", True, {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}, "llm"

        with patch("workflows.complex_flow.replan_next_sql_step", side_effect=fake_replan):
            with patch(
                "workflows.complex_flow.execute_sql",
                side_effect=lambda sql: {"rows": [("player", 10)], "row_count": 1},
            ):
                step_replan_execute_loop(ctx)

        self.assertEqual(len(ctx.metadata["sql_step_results"]), 2)
        self.assertEqual(ctx.metadata["plan_method"], "replan")
        self.assertEqual(ctx.metadata["tools_used"], ["sql_query", "sql_query"])
        self.assertGreaterEqual(ctx.metadata["replan_rounds"], 2)
        self.assertIsNotNone(ctx.metadata.get("sql_generated"))

    def test_heuristic_no_default_for_unmatched_query(self) -> None:
        plans = _heuristic_sql_plans("随便聊聊世界杯气氛")
        self.assertEqual(plans, [])

    def test_heuristic_ranking_without_position(self) -> None:
        plans = _heuristic_sql_plans("男子世界杯进球最多的球员是谁？")
        self.assertEqual(len(plans), 1)
        self.assertIn("ORDER BY goals DESC", plans[0]["sql"])

    def test_replan_loop_sets_sql_generated_on_heuristic_skip(self) -> None:
        ctx = WorkflowContext(query="梅西和C罗谁世界杯进球更多？")
        with patch(
            "workflows.complex_flow.replan_next_sql_step",
            return_value=(None, "", True, {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}, "skipped"),
        ):
            with patch(
                "workflows.complex_flow.execute_sql",
                side_effect=lambda sql: {"rows": [(1,)], "row_count": 1},
            ):
                step_replan_execute_loop(ctx)
        self.assertIn("vw_player_summary", ctx.metadata["sql_generated"] or "")

    def test_heuristic_empty_triggers_semantic_search(self) -> None:
        ctx = WorkflowContext(query="随便聊聊世界杯气氛")
        with patch(
            "workflows.complex_flow.replan_next_sql_step",
            return_value=(None, "", True, {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}, "skipped"),
        ):
            with patch(
                "workflows.complex_flow.semantic_search",
                return_value=[
                    {
                        "collection": "worldcup-tournaments",
                        "external_id": "2022",
                        "content": "Winner: Argentina",
                        "similarity": 0.9,
                    }
                ],
            ) as mock_semantic:
                step_replan_execute_loop(ctx)
        mock_semantic.assert_called_once()
        self.assertEqual(ctx.metadata["plan_method"], "heuristic_empty")
        self.assertIn("semantic_search", ctx.metadata["tools_used"])

    def test_replan_loop_uses_heuristic_when_llm_skipped(self) -> None:
        ctx = WorkflowContext(query="梅西和C罗谁世界杯进球更多？")
        with patch(
            "workflows.complex_flow.replan_next_sql_step",
            return_value=(None, "", True, {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}, "skipped"),
        ):
            with patch(
                "workflows.complex_flow.execute_sql",
                side_effect=lambda sql: {"rows": [(1,)], "row_count": 1},
            ):
                step_replan_execute_loop(ctx)
        self.assertEqual(ctx.metadata["plan_method"], "heuristic")
        self.assertGreaterEqual(len(ctx.metadata["sql_step_results"]), 2)
        self.assertLessEqual(len(ctx.metadata["sql_step_results"]), MAX_SQL_STEPS)

    def test_replan_loop_caps_at_max_sql_steps(self) -> None:
        ctx = WorkflowContext(query="一直查")

        def never_done(query, executed_steps, **kwargs):
            return (
                {
                    "purpose": f"step_{len(executed_steps) + 1}",
                    "sql": f"SELECT {len(executed_steps) + 1} AS n",
                },
                "继续",
                False,
                {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
                "llm",
            )

        with patch("workflows.complex_flow.replan_next_sql_step", side_effect=never_done):
            with patch(
                "workflows.complex_flow.execute_sql",
                side_effect=lambda sql: {"rows": [(1,)], "row_count": 1},
            ):
                step_replan_execute_loop(ctx)

        self.assertEqual(len(ctx.metadata["sql_step_results"]), MAX_SQL_STEPS)
        self.assertLessEqual(ctx.metadata["replan_rounds"], MAX_REPLAN_ROUNDS)

    def test_replan_llm_done_without_sql_skips_heuristic(self) -> None:
        ctx = WorkflowContext(query="世界杯有什么好玩的故事？")
        empty = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}
        with patch(
            "workflows.complex_flow.replan_next_sql_step",
            return_value=(None, "无需 SQL", True, empty, "llm"),
        ):
            with patch("workflows.complex_flow._execute_heuristic_batch") as mock_heuristic:
                with patch(
                    "workflows.complex_flow.semantic_search",
                    return_value=[
                        {
                            "collection": "worldcup-tournaments",
                            "external_id": "2022",
                            "content": "A classic story",
                            "similarity": 0.85,
                        }
                    ],
                ):
                    step_replan_execute_loop(ctx)
        mock_heuristic.assert_not_called()
        self.assertEqual(ctx.metadata["plan_method"], "replan_done")
        self.assertEqual(ctx.metadata["sql_step_results"], [])
        self.assertIn("semantic_search", ctx.metadata["tools_used"])

    def test_replan_invalid_step_uses_heuristic(self) -> None:
        ctx = WorkflowContext(query="梅西和C罗谁世界杯进球更多？")
        empty = {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}
        with patch(
            "workflows.complex_flow.replan_next_sql_step",
            return_value=(None, "bad sql", True, empty, "invalid_step"),
        ):
            with patch(
                "workflows.complex_flow.execute_sql",
                side_effect=lambda sql: {"rows": [(1,)], "row_count": 1},
            ):
                step_replan_execute_loop(ctx)
        self.assertEqual(ctx.metadata["plan_method"], "heuristic")
        self.assertGreaterEqual(len(ctx.metadata["sql_step_results"]), 2)


if __name__ == "__main__":
    unittest.main()
