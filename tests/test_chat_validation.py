"""Unit tests for chat validation."""

from __future__ import annotations

import unittest

from core.chat_validation import (
    HISTORY_SESSION_CONFLICT,
    validate_history_session_conflict,
)


class ChatValidationTests(unittest.TestCase):
    def test_allows_session_only(self):
        validate_history_session_conflict(session_id="user-1", history=None)

    def test_allows_history_only(self):
        validate_history_session_conflict(session_id=None, history=[{"user": "hi", "assistant": "yo"}])

    def test_rejects_session_with_empty_history_list(self):
        with self.assertRaises(ValueError):
            validate_history_session_conflict(session_id="user-1", history=[])

    def test_rejects_both(self):
        with self.assertRaises(ValueError) as ctx:
            validate_history_session_conflict(
                session_id="user-1",
                history=[{"user": "hi", "assistant": "yo"}],
            )
        self.assertIn(HISTORY_SESSION_CONFLICT, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
