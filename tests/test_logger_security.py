"""JSON log formatter sanitizes structured context."""

from __future__ import annotations

import json
import logging
import unittest

from core.logger import JSONFormatter


class JSONFormatterSecurityTests(unittest.TestCase):
    def test_formats_sanitized_context_and_exception(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="worldcup-rag.test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="error sk-abcdefghijklmnopqrstuvwxyz1234567890",
            args=(),
            exc_info=None,
        )
        record.log_context = {"sql_preview": "contact 13812345678"}
        record.exc_text = "boom sk-abcdefghijklmnopqrstuvwxyz1234567890"

        payload = json.loads(formatter.format(record))
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890", payload["message"])
        self.assertNotIn("13812345678", payload["context"]["sql_preview"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890", payload["exception"])


if __name__ == "__main__":
    unittest.main()
