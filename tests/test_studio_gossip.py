"""Gossip Studio Assistant context tests (skip steps / disable tools)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from workflows.base import WorkflowContext
from workflows.gossip import (
    apply_gossip_studio_controls,
    apply_gossip_studio_skip,
    step_enrich_player_context,
    step_retrieve_stories,
)
from workflows.studio_context import GossipStudioContext, GOSSIP_STUDIO_STEP_NAMES


class GossipStudioContextTests(unittest.TestCase):
    def test_schema_lists_gossip_step_names(self) -> None:
        props = GossipStudioContext.model_json_schema()["properties"]["skip_steps"]
        nodes = props.get("langgraph_nodes") or []
        self.assertEqual(set(nodes), set(GOSSIP_STUDIO_STEP_NAMES))

    def test_apply_skip_retrieve_sets_defaults(self) -> None:
        ctx = WorkflowContext(query="某球员八卦", metadata={"tools_trace": ["classify_topic"]})
        apply_gossip_studio_skip("step_retrieve_stories", ctx)
        self.assertEqual(ctx.metadata["story_hits"], [])
        self.assertIn("retrieve_skipped", ctx.metadata["tools_trace"])
        self.assertIn("step_retrieve_stories", ctx.metadata["studio_skipped_steps"])

    @patch("workflows.gossip.semantic_search")
    def test_disable_semantic_search_skips_search(self, mock_search) -> None:
        ctx = WorkflowContext(
            query="世界杯有哪些趣闻",
            metadata={
                "tools_trace": ["classify_topic"],
                "gossip_analysis": {"topics": ["gossip"]},
                "studio_enable_semantic_search": False,
            },
        )
        step_retrieve_stories(ctx)
        mock_search.assert_not_called()
        self.assertEqual(ctx.metadata["story_hits"], [])
        self.assertIn("retrieve_skipped", ctx.metadata["tools_trace"])

    @patch("workflows.gossip.get_player_stats")
    def test_disable_player_stats_skips_enrich(self, mock_stats) -> None:
        ctx = WorkflowContext(
            query="梅西八卦",
            metadata={
                "tools_trace": ["classify_topic"],
                "player_mentions": ["梅西"],
                "player_context": [{"mention": "梅西", "player_id": "P-1", "preview": "stale"}],
                "studio_enable_player_stats": False,
            },
        )
        step_enrich_player_context(ctx)
        mock_stats.assert_not_called()
        self.assertEqual(ctx.metadata["player_context"], [])

    def test_apply_controls_writes_metadata_flags(self) -> None:
        ctx = WorkflowContext(query="test")
        apply_gossip_studio_controls(
            ctx,
            enable_semantic_search=False,
            enable_player_stats=True,
        )
        self.assertFalse(ctx.metadata["studio_enable_semantic_search"])
        self.assertTrue(ctx.metadata["studio_enable_player_stats"])

    @patch("workflows.gossip.compose_gossip_reply")
    @patch("workflows.gossip.semantic_search")
    def test_gossip_graph_skips_step_from_assistant(
        self,
        mock_search,
        mock_compose,
    ) -> None:
        from workflows.studio_graphs import gossip_graph

        mock_search.return_value = [{"content": "story"}]
        mock_compose.return_value = ("composed", {}, "template")

        result = gossip_graph.invoke(
            {"query": "世界杯趣闻"},
            context=GossipStudioContext(skip_steps=["step_retrieve_stories"]),
        )
        mock_search.assert_not_called()
        mock_compose.assert_called_once()
        self.assertEqual(result.get("answer"), "composed")
        self.assertIn("step_retrieve_stories", result["metadata"]["studio_skipped_steps"])


if __name__ == "__main__":
    unittest.main()
