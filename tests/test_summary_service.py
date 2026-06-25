"""Tests for session summary compression helpers."""

import unittest
from unittest.mock import MagicMock, patch

from core.summary_service import maybe_compress_session


class SummaryServiceTests(unittest.TestCase):
    @patch("core.summary_service._summary_llm")
    @patch("core.summary_service.get_session_memory")
    @patch("core.summary_service.get_queue_config")
    def test_compress_when_threshold_met(self, mock_cfg, mock_memory_fn, mock_llm_fn):
        mock_cfg.return_value.summary.enabled = True
        mock_cfg.return_value.summary.compress_after_turns = 2
        mock_cfg.return_value.summary.token_threshold = 100
        mock_cfg.return_value.summary.keep_recent_turns = 1

        memory = MagicMock()
        memory.available = True
        memory.should_compress_summary.return_value = True
        memory.get_context.return_value = (
            [
                {"role": "user", "content": "梅西进了几个球"},
                {"role": "assistant", "content": "13个"},
                {"role": "user", "content": "C罗呢"},
                {"role": "assistant", "content": "8个"},
            ],
            "",
            [],
        )
        memory.set_summary.return_value = True
        memory.archive_summarized_messages.return_value = 2
        mock_memory_fn.return_value = memory

        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="梅西13球，C罗8球")
        mock_llm_fn.return_value = llm

        ok = maybe_compress_session("sess-1", "simple_qa", trace_id="t1")
        self.assertTrue(ok)
        memory.set_summary.assert_called_once()
        memory.archive_summarized_messages.assert_called_once()


if __name__ == "__main__":
    unittest.main()
