"""Unit tests for memory_persisted resolution."""

from __future__ import annotations

import unittest

from core.memory_status import resolve_memory_persisted


class MemoryPersistedStatusTests(unittest.TestCase):
    def test_no_session_returns_none(self):
        self.assertIsNone(resolve_memory_persisted(None, persisted=True))
        self.assertIsNone(resolve_memory_persisted(None, persisted=False))

    def test_session_success(self):
        self.assertTrue(resolve_memory_persisted("s1", persisted=True))

    def test_session_failure(self):
        self.assertFalse(resolve_memory_persisted("s1", persisted=False))
        self.assertFalse(resolve_memory_persisted("s1", persisted=None))

    def test_session_skipped(self):
        self.assertFalse(resolve_memory_persisted("s1", persisted=None, skipped=True))


if __name__ == "__main__":
    unittest.main()
