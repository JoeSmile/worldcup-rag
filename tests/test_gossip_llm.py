"""Gossip LLM compose tests."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from workflows.gossip_llm import compose_gossip_reply, template_compose_reply


class GossipLLMTests(unittest.TestCase):
    def test_template_identity_fast_path(self) -> None:
        text = template_compose_reply("你谁啊", [], [], [], "identity")
        self.assertIn("世界杯", text)

    @patch("workflows.gossip_llm.settings")
    @patch("workflows.gossip_llm._get_gossip_llm")
    def test_compose_gossip_reply_llm_path(self, mock_get_llm, mock_settings) -> None:
        mock_settings.llm_api_key = "test-key"
        mock_settings.router_model_name = "qwen-turbo"
        mock_settings.langsmith_run_config = lambda *args, **kwargs: {}

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content="LLM 回复",
            response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        )
        mock_get_llm.return_value = (mock_llm, MagicMock(), MagicMock())

        answer, usage, method = compose_gossip_reply(
            "你谁啊",
            ["casual_football"],
            [],
            [],
            fast_path="identity",
        )
        self.assertEqual(answer, "LLM 回复")
        self.assertEqual(method, "llm")
        self.assertEqual(usage["total_tokens"], 15)
        mock_llm.invoke.assert_called_once()

    @patch("workflows.gossip_llm.settings")
    def test_compose_gossip_reply_template_when_no_key(self, mock_settings) -> None:
        mock_settings.llm_api_key = None
        answer, usage, method = compose_gossip_reply("你谁啊", [], [], [], fast_path="identity")
        self.assertEqual(method, "template")
        self.assertIn("世界杯", answer)
        self.assertEqual(usage["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
