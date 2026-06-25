"""Unit tests for chat response enrichment."""

from __future__ import annotations

import unittest

from core.chat_response import enrich_chat_result


class EnrichChatResultTests(unittest.TestCase):
    def test_no_session_leaves_memory_persisted_null(self):
        result = enrich_chat_result({"answer": "ok"}, None)
        self.assertIsNone(result["memory_persisted"])
        self.assertNotIn("session_id", result)

    def test_error_payload_gets_session_fields(self):
        result = enrich_chat_result(
            {"answer": "抱歉", "error": "boom", "memory_persisted": False},
            "user-1",
        )
        self.assertEqual(result["session_id"], "user-1")
        self.assertFalse(result["memory_persisted"])


if __name__ == "__main__":
    unittest.main()
