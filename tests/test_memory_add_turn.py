"""Unit tests for add_turn rollback behavior."""

from __future__ import annotations

import unittest
from unittest.mock import ANY, MagicMock, patch

from redis.exceptions import RedisError

from core.memory import SessionMemory


class AddTurnRollbackTests(unittest.TestCase):
    @patch("core.memory.get_redis_text")
    def test_rollback_on_assistant_write_failure(self, mock_get_redis):
        redis = MagicMock()
        mock_get_redis.return_value = redis
        lock = MagicMock()
        lock.__enter__ = MagicMock(return_value=None)
        lock.__exit__ = MagicMock(return_value=None)
        redis.lock.return_value = lock

        memory = SessionMemory()
        calls = {"write": 0}

        def write_side_effect(session_id, role, content, *, workflow, tokens):
            calls["write"] += 1
            if role == "user":
                return "user-msg-id"
            return None

        memory._write_message_locked = write_side_effect  # type: ignore[method-assign]
        memory._rollback_message = MagicMock()  # type: ignore[method-assign]

        ok = memory.add_turn("sess-1", "hello", "world", workflow="simple_qa")

        self.assertFalse(ok)
        memory._rollback_message.assert_called_once_with("sess-1", "user-msg-id", ANY)


    @patch("core.memory.get_redis_text")
    def test_rollback_on_redis_exception_after_user_write(self, mock_get_redis):
        redis = MagicMock()
        mock_get_redis.return_value = redis
        lock = MagicMock()
        lock.__enter__ = MagicMock(return_value=None)
        lock.__exit__ = MagicMock(return_value=None)
        redis.lock.return_value = lock

        memory = SessionMemory()
        calls = {"write": 0}

        def write_side_effect(session_id, role, content, *, workflow, tokens):
            calls["write"] += 1
            if role == "user":
                return "user-msg-id"
            raise RedisError("assistant write failed")

        memory._write_message_locked = write_side_effect  # type: ignore[method-assign]
        memory._rollback_message = MagicMock()  # type: ignore[method-assign]

        ok = memory.add_turn("sess-1", "hello", "world", workflow="simple_qa")

        self.assertFalse(ok)
        memory._rollback_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
