"""Studio graph routing and state tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from workflows.base import WorkflowContext
from workflows.gossip_llm import template_compose_reply
from workflows.studio_graphs import (
    _canonical_studio_output,
    _finalize_simple_qa_output,
    _finalize_step_output,
    _state_from_ctx,
    _run_worldcup_chat,
)


class StudioGraphTests(unittest.TestCase):
    def test_state_from_ctx_exposes_canonical_fields(self) -> None:
        ctx = WorkflowContext(query="test question")
        ctx.set_answer("hello", tool_name="player_stats", tools_used=["player_stats"])
        state = _state_from_ctx(ctx, "gossip")
        self.assertEqual(state.get("answer"), "hello")
        self.assertEqual(state.get("workflow"), "gossip")
        self.assertEqual(state.get("graph"), "gossip")
        self.assertEqual(state.get("tools_used"), ["player_stats"])
        self.assertEqual(state.get("tool_name"), "player_stats")

    def test_finalize_step_output(self) -> None:
        patch = _finalize_step_output(
            {
                "query": "你谁啊",
                "answer": "我是助手",
                "metadata": {"tools_used": ["classify_topic"], "tool_name": None},
            },
            "gossip",
        )
        self.assertEqual(patch["workflow"], "gossip")
        self.assertEqual(patch["answer"], "我是助手")
        self.assertEqual(patch["tools_used"], ["classify_topic"])
        self.assertEqual(patch["graph"], "gossip")

    def test_finalize_simple_qa_output_from_messages(self) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        messages = [
            HumanMessage(content="梅西进了几个球"),
            AIMessage(
                content="",
                tool_calls=[{"name": "player_stats", "args": {"name": "梅西"}, "id": "1"}],
            ),
            ToolMessage(content="[]", tool_call_id="1"),
            AIMessage(content="13球"),
        ]
        patch = _finalize_simple_qa_output({"messages": messages})
        self.assertEqual(patch["workflow"], "simple_qa")
        self.assertEqual(patch["answer"], "13球")
        self.assertEqual(patch["tools_used"], ["player_stats"])
        self.assertEqual(patch["tool_name"], "player_stats")
        self.assertIn("梅西", patch["query"])

    def test_worldcup_chat_canonical_output(self) -> None:
        out = _canonical_studio_output(
            query="q",
            answer="a",
            workflow="simple_qa",
            tools_used=["player_stats"],
            tool_name="player_stats",
        )
        self.assertEqual(out["graph"], "simple_qa")
        self.assertEqual(out["workflow"], "simple_qa")
        self.assertEqual(out["tools_used"], ["player_stats"])

    @patch("agent.chat")
    def test_worldcup_chat_accepts_graph_field(self, mock_chat) -> None:
        mock_chat.return_value = {
            "answer": "13",
            "workflow": "simple_qa",
            "tools_used": ["player_stats"],
            "error": None,
        }
        result = _run_worldcup_chat(
            {"query": "梅西进了几个球", "graph": "simple_qa"},
        )
        mock_chat.assert_called_once()
        self.assertEqual(mock_chat.call_args.kwargs.get("workflow"), "simple_qa")
        self.assertEqual(result.get("answer"), "13")

    @patch("agent.chat")
    def test_worldcup_chat_accepts_workflow_alias(self, mock_chat) -> None:
        mock_chat.return_value = {
            "answer": "ok",
            "workflow": "gossip",
            "tools_used": [],
            "error": None,
        }
        result = _run_worldcup_chat(
            {"query": "你谁啊", "workflow": "gossip"},
        )
        mock_chat.assert_called_once()
        self.assertEqual(mock_chat.call_args.kwargs.get("workflow"), "gossip")
        self.assertEqual(result.get("answer"), "ok")

    def test_template_casual_no_hint(self) -> None:
        text = template_compose_reply("随便聊聊", ["casual_football"], [], [], "casual_no_hint")
        self.assertIn("世界杯", text)


if __name__ == "__main__":
    unittest.main()
