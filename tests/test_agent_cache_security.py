"""Agent cache path applies outbound redaction on hits."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agent import chat


class AgentCacheSecurityTests(unittest.TestCase):
    @patch("agent.workflow_chat")
    @patch("agent.get_query_cache")
    @patch("agent.get_cache_config")
    def test_cache_hit_redacts_before_return(self, mock_cache_cfg, mock_get_cache, mock_workflow):
        mock_cache_cfg.return_value.enabled = True
        cached = {"answer": "tel 13812345678", "workflow": "simple_qa"}
        mock_get_cache.return_value.get.return_value = (cached, "l2")

        result = chat("cached question")

        mock_workflow.assert_not_called()
        self.assertNotIn("13812345678", result["answer"])
        self.assertTrue(result.get("cache_hit"))


if __name__ == "__main__":
    unittest.main()
