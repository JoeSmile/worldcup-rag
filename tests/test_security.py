"""Unit tests for security filter and SQL safety."""

from __future__ import annotations

import unittest

from core.security import SecurityFilter


class SecurityFilterTests(unittest.TestCase):
    def test_sanitize_api_key(self):
        raw = "my key sk-abcdefghijklmnopqrstuvwxyz1234567890"
        cleaned = SecurityFilter.sanitize_text(raw)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    def test_sanitize_preserves_player_names(self):
        text = "梅西在2018年世界杯的表现"
        self.assertEqual(SecurityFilter.sanitize_text(text), text)

    def test_safe_select_sql(self):
        sql = "SELECT display_name FROM vw_player_summary WHERE goals > 0 LIMIT 5"
        self.assertFalse(SecurityFilter.is_unsafe_sql(sql))

    def test_rejects_stacked_sql(self):
        sql = "SELECT 1; DROP TABLE players"
        self.assertTrue(SecurityFilter.is_unsafe_sql(sql))

    def test_rejects_union_select(self):
        sql = "SELECT a FROM t1 UNION SELECT b FROM t2"
        self.assertTrue(SecurityFilter.is_unsafe_sql(sql))

    def test_redact_chat_result_strips_phone(self):
        raw = {"answer": "call 13812345678", "sql_generated": "SELECT 1"}
        redacted = SecurityFilter.redact_chat_result(raw)
        self.assertNotIn("13812345678", redacted["answer"])

    def test_rejects_non_select(self):
        self.assertTrue(SecurityFilter.is_unsafe_sql("DELETE FROM vw_player_summary"))

    def test_user_injection_pattern(self):
        self.assertTrue(SecurityFilter.looks_like_sql_injection_in_query("'; DROP TABLE users--"))
        self.assertTrue(SecurityFilter.looks_like_sql_injection_in_query("1 UNION SELECT password FROM users"))
        self.assertFalse(SecurityFilter.looks_like_sql_injection_in_query("梅西进了几个球"))

    def test_safe_error_detail_redacts_secrets(self):
        detail = SecurityFilter.safe_error_detail("failed sk-abcdefghijklmnopqrstuvwxyz1234567890")
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890", detail)

    def test_redact_chat_result_respects_scan_output_flag(self):
        from unittest.mock import patch

        raw = {"answer": "tel 13812345678"}
        with patch("core.security.SecurityFilter._cfg") as mock_cfg:
            mock_cfg.return_value.enabled = True
            mock_cfg.return_value.scan_output = False
            self.assertEqual(SecurityFilter.redact_chat_result(raw), raw)

    def test_sanitize_deep_nested_dict(self):
        payload = {"answer": "tel 13812345678", "nested": {"sql_preview": "err sk-abcdefghijklmnopqrstuvwxyz1234567890"}}
        sanitized = SecurityFilter.sanitize_deep(payload)
        self.assertNotIn("13812345678", sanitized["answer"])
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz1234567890", sanitized["nested"]["sql_preview"])

    def test_scan_output_flags_phone(self):
        result = SecurityFilter.scan_output("联系客服 13812345678")
        self.assertFalse(result.safe)
        self.assertTrue(any(i["type"] == "phone_cn" for i in result.issues))

    def test_scan_and_redact_answer(self):
        answer, scan = SecurityFilter.scan_and_redact_text("tel 13812345678")
        self.assertFalse(scan.safe)
        self.assertNotIn("13812345678", answer)

    def test_entropy_scan_flags_random_secret(self):
        secret = "aB3xK9mN2pQ7rT5vW8yZ1cD4fG6hJ0"
        result = SecurityFilter.scan_output(f"token={secret}")
        self.assertFalse(result.safe)
        self.assertTrue(any(i["type"] == "high_entropy_secret" for i in result.issues))

    def test_entropy_scan_ignores_player_sentence(self):
        text = "梅西在2018年世界杯决赛中打进两球"
        result = SecurityFilter.scan_output(text)
        self.assertTrue(result.safe)

    def test_scan_chat_response_includes_sql_generated(self):
        payload = {
            "answer": "ok",
            "sql_generated": "SELECT 1; contact 13812345678",
        }
        updated, scan = SecurityFilter.scan_and_redact_chat_response(payload)
        self.assertFalse(scan.safe)
        self.assertNotIn("13812345678", updated["sql_generated"])


if __name__ == "__main__":
    unittest.main()
