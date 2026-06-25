"""Tests for post-chat queue scheduling."""

import unittest
from unittest.mock import patch

from core.post_chat_queue import enqueue_post_chat_tasks


class PostChatEnqueueTests(unittest.TestCase):
    @patch("core.post_chat_queue.enqueue_summary_compress")
    @patch("core.post_chat_queue.enqueue_cache_write")
    @patch("core.post_chat_queue.get_queue_config")
    def test_schedules_cache_and_summary(self, mock_cfg, mock_cache, mock_summary):
        mock_cfg.return_value.queue.enabled = True
        mock_cfg.return_value.queue.defer_cache_write = True
        mock_cfg.return_value.summary.enabled = True
        mock_cache.return_value = "1-0"
        mock_summary.return_value = "2-0"

        result = {
            "workflow": "simple_qa",
            "memory_persisted": True,
            "answer": "13",
        }
        scheduled = enqueue_post_chat_tasks(
            "梅西进了几个球",
            result,
            trace_id="t1",
            session_id="sess-1",
            use_query_cache=True,
        )
        self.assertTrue(scheduled["cache_write"])
        self.assertTrue(scheduled["summary_compress"])
        mock_cache.assert_called_once()
        mock_summary.assert_called_once()


if __name__ == "__main__":
    unittest.main()
