"""Studio context and simple_qa prompt override tests."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from prompts import SYSTEM_PROMPT
from workflows.simple_qa import _build_agent, build_simple_qa_messages, get_agent_for_prompt
from workflows.studio_context import StudioContext
from workflows.studio_graphs import _normalize_simple_qa_input, _run_simple_qa_agent


class StudioContextTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_agent_for_prompt.cache_clear()

    def test_default_prompt_matches_system_prompt(self) -> None:
        ctx = StudioContext()
        self.assertEqual(ctx.simple_qa_system_prompt, SYSTEM_PROMPT)

    def test_schema_marks_prompt_field(self) -> None:
        props = StudioContext.model_json_schema()["properties"]["simple_qa_system_prompt"]
        self.assertEqual(props.get("langgraph_type"), "prompt")
        self.assertEqual(props.get("langgraph_nodes"), ["agent"])

    def test_build_simple_qa_messages_includes_history(self) -> None:
        messages = build_simple_qa_messages(
            "他呢？",
            history=[
                {"user": "梅西进了几个球", "assistant": "13 个"},
            ],
        )
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]["content"], "梅西进了几个球")
        self.assertEqual(messages[-1]["content"], "他呢？")

    @patch("workflows.simple_qa.create_agent")
    def test_build_agent_uses_custom_prompt(self, mock_create) -> None:
        mock_create.return_value = MagicMock()
        _build_agent(system_prompt="custom prompt")
        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs["system_prompt"], "custom prompt")

    @patch("workflows.simple_qa.create_agent")
    def test_build_agent_empty_prompt_falls_back_to_system_prompt(self, mock_create) -> None:
        mock_create.return_value = MagicMock()
        _build_agent(system_prompt="")
        self.assertEqual(mock_create.call_args.kwargs["system_prompt"], SYSTEM_PROMPT)

    @patch("workflows.simple_qa.create_agent")
    def test_get_agent_for_prompt_caches_by_prompt(self, mock_create) -> None:
        mock_create.side_effect = [MagicMock(name="agent-a"), MagicMock(name="agent-b")]
        get_agent_for_prompt("prompt-a")
        get_agent_for_prompt("prompt-a")
        get_agent_for_prompt("prompt-b")
        self.assertEqual(mock_create.call_count, 2)

    def test_normalize_simple_qa_input_merges_history(self) -> None:
        patch = _normalize_simple_qa_input(
            {
                "query": "他呢？",
                "history": [{"user": "梅西进了几个球", "assistant": "13 个"}],
            },
        )
        self.assertEqual(len(patch["messages"]), 3)
        self.assertEqual(patch["messages"][-1]["content"], "他呢？")

    @patch("workflows.studio_graphs.get_agent_for_prompt")
    def test_run_simple_qa_agent_reads_studio_context(self, mock_get_agent) -> None:
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [{"role": "assistant", "content": "ok"}]}
        mock_get_agent.return_value = mock_agent

        runtime = MagicMock()
        runtime.context = StudioContext(simple_qa_system_prompt="studio prompt")

        result = _run_simple_qa_agent(
            {"messages": [{"role": "user", "content": "hi"}]},
            runtime,
        )
        mock_get_agent.assert_called_once_with("studio prompt")
        mock_agent.invoke.assert_called_once()
        self.assertIsNone(result.get("error"))

    @patch("workflows.studio_graphs.get_agent_for_prompt")
    def test_run_simple_qa_agent_returns_error_on_failure(self, mock_get_agent) -> None:
        mock_agent = MagicMock()
        mock_agent.invoke.side_effect = RuntimeError("boom")
        mock_get_agent.return_value = mock_agent

        runtime = MagicMock()
        runtime.context = StudioContext()

        messages = [{"role": "user", "content": "hi"}]
        result = _run_simple_qa_agent({"messages": messages}, runtime)
        self.assertEqual(result["messages"], messages)
        self.assertEqual(result["error"], "boom")


if __name__ == "__main__":
    unittest.main()
