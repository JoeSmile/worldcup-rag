"""Gossip workflow smoke tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from workflows.base import WorkflowContext
from workflows.gossip import (
    _needs_story_retrieval,
    step_classify_topic,
    step_compose_reply,
    step_retrieve_stories,
)


class GossipWorkflowTests(unittest.TestCase):
    def test_classify_topic_recognizes_gossip_keywords(self) -> None:
        ctx = WorkflowContext(query="有什么世界杯八卦？")
        step_classify_topic(ctx)
        self.assertIn("gossip", ctx.metadata["gossip_analysis"]["topics"])

    def test_identity_query_skips_retrieval(self) -> None:
        ctx = WorkflowContext(query="你谁啊")
        step_classify_topic(ctx)
        self.assertFalse(_needs_story_retrieval(ctx))
        self.assertEqual(ctx.metadata["gossip_fast_path"], "identity")

        with patch("workflows.gossip.semantic_search") as mock_search:
            step_retrieve_stories(ctx)
            mock_search.assert_not_called()

        self.assertEqual(ctx.metadata["story_hits"], [])
        self.assertIn("retrieve_skipped", ctx.metadata["tools_trace"])

    def test_identity_plus_football_question_still_retrieves(self) -> None:
        ctx = WorkflowContext(query="你是谁？梅西世界杯进了几个球？")
        step_classify_topic(ctx)
        self.assertTrue(_needs_story_retrieval(ctx))
        self.assertIsNone(ctx.metadata.get("gossip_fast_path"))

    def test_gossip_keyword_still_retrieves(self) -> None:
        ctx = WorkflowContext(query="有什么世界杯八卦？")
        step_classify_topic(ctx)
        self.assertTrue(_needs_story_retrieval(ctx))

    def test_compose_reply_uses_llm_when_available(self) -> None:
        ctx = WorkflowContext(query="你谁啊")
        step_classify_topic(ctx)
        step_retrieve_stories(ctx)

        with patch("workflows.gossip.compose_gossip_reply", return_value=("LLM 自我介绍", {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}, "llm")):
            step_compose_reply(ctx)

        self.assertEqual(ctx.metadata.get("answer"), "LLM 自我介绍")
        self.assertEqual(ctx.metadata.get("compose_method"), "llm")
        self.assertEqual(ctx.metadata.get("usage", {}).get("total_tokens"), 10)

    def test_compose_reply_tool_name_when_search_returns_empty(self) -> None:
        ctx = WorkflowContext(query="有什么世界杯八卦？")
        step_classify_topic(ctx)
        with patch("workflows.gossip.semantic_search", return_value=[]):
            step_retrieve_stories(ctx)
        self.assertIn("semantic_search", ctx.metadata["tools_trace"])

        with patch(
            "workflows.gossip.compose_gossip_reply",
            return_value=("无素材", {}, "template"),
        ):
            step_compose_reply(ctx)

        self.assertEqual(ctx.metadata.get("tool_name"), "semantic_search")
        self.assertEqual(ctx.metadata.get("tools_used"), ["semantic_search"])
        self.assertIn("classify_topic", ctx.metadata.get("tools_trace", []))
        self.assertEqual(ctx.metadata.get("story_hit_count"), 0)

    def test_compose_reply_tools_used_excludes_internal_steps(self) -> None:
        ctx = WorkflowContext(query="你谁啊")
        step_classify_topic(ctx)
        step_retrieve_stories(ctx)
        with patch(
            "workflows.gossip.compose_gossip_reply",
            return_value=("hi", {}, "template"),
        ):
            step_compose_reply(ctx)
        self.assertEqual(ctx.metadata.get("tools_used"), [])
        self.assertIn("classify_topic", ctx.metadata.get("tools_trace", []))

if __name__ == "__main__":
    unittest.main()
