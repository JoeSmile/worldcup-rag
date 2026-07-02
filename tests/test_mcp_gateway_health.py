"""Tests for MCP Gateway health probes."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.mcp_gateway_config import get_mcp_gateway_config
from core.mcp_gateway_health import get_mcp_gateway_status, probe_gateway_http, startup_alert


class McpGatewayHealthTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_mcp_gateway_config.cache_clear()

    def test_probe_fails_when_nothing_listening(self) -> None:
        ok, detail = probe_gateway_http(
            health_url="http://127.0.0.1:59999/health",
            timeout=0.3,
        )
        self.assertFalse(ok)
        self.assertTrue(detail)

    @patch.dict(
        os.environ,
        {
            "MCP_GATEWAY_ENABLED": "true",
            "MCP_GATEWAY_URL": "http://localhost:8080/mcp",
            "MCP_GATEWAY_EMBED_PROCESS": "false",
        },
    )
    def test_startup_alert_when_remote_gateway_down(self) -> None:
        get_mcp_gateway_config.cache_clear()
        alert = startup_alert()
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.get("status"), "down")

    @patch.dict(os.environ, {"MCP_GATEWAY_ENABLED": "false"})
    def test_no_alert_when_disabled(self) -> None:
        get_mcp_gateway_config.cache_clear()
        self.assertIsNone(startup_alert())
        status = get_mcp_gateway_status()
        self.assertEqual(status.get("status"), "disabled")


if __name__ == "__main__":
    unittest.main()
