"""Tests for external lookup routing and MCP Gateway Mode A workflow."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from core.mcp_gateway_config import get_mcp_gateway_config
from workflows.external_lookup import needs_external_lookup, run_external_mcp_lookup
from workflows.external_qa import external_qa_workflow
from workflows.router import route


class ExternalLookupDetectionTests(unittest.TestCase):
    def test_detects_2026_keyword(self) -> None:
        self.assertTrue(needs_external_lookup("2026世界杯主办国是哪里？"))

    def test_detects_future_year_with_topic(self) -> None:
        self.assertTrue(needs_external_lookup("2027世界杯赛程公布了吗？"))

    def test_ignores_historical_year_without_external_signal(self) -> None:
        self.assertFalse(needs_external_lookup("2018年世界杯冠军是谁？"))


class ExternalRouterTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_mcp_gateway_config.cache_clear()

    def test_routes_external_when_gateway_enabled(self) -> None:
        with patch.dict(os.environ, {"MCP_GATEWAY_ENABLED": "true"}):
            get_mcp_gateway_config.cache_clear()
            self.assertEqual(route("2026世界杯有多少支球队？"), "external_qa")

    def test_skips_external_when_gateway_disabled(self) -> None:
        with patch.dict(os.environ, {"MCP_GATEWAY_ENABLED": "false"}):
            get_mcp_gateway_config.cache_clear()
            self.assertEqual(route("2026世界杯有多少支球队？"), "simple_qa")

    def test_gossip_still_wins_over_external(self) -> None:
        with patch.dict(os.environ, {"MCP_GATEWAY_ENABLED": "true"}):
            get_mcp_gateway_config.cache_clear()
            self.assertEqual(route("2026世界杯八卦"), "gossip")


class ExternalQaWorkflowTests(unittest.TestCase):
    @patch("workflows.external_qa.run_external_mcp_lookup")
    def test_workflow_uses_gateway_payload(self, mock_lookup) -> None:
        mock_lookup.return_value = {
            "text": '{"facts":["stub"]}',
            "parsed": {"facts": ["stub"]},
            "tool_trace": [
                "mcp_gateway",
                "mcp:list_servers",
                "mcp:execute_tool:worldcup-live/search_live",
            ],
            "server": "worldcup-live",
            "tool": "search_live",
        }
        result = external_qa_workflow.run("2026世界杯主办国？")
        self.assertEqual(result.get("workflow"), "external_qa")
        self.assertIn("mcp:execute_tool:worldcup-live/search_live", result.get("tools_used") or [])
        self.assertIsNone(result.get("error"))
        self.assertTrue(result.get("answer"))


class ExternalMcpLookupTests(unittest.TestCase):
    @patch("workflows.external_lookup.plan_gateway_invocation")
    @patch("workflows.external_lookup.get_mcp_gateway_client")
    def test_run_external_mcp_lookup_uses_gateway_flow(
        self,
        mock_get_client,
        mock_plan,
    ) -> None:
        client = mock_get_client.return_value
        client.call_gateway_tool.side_effect = [
            json.dumps([{"name": "worldcup-live", "description": "live"}]),
            json.dumps({"tools": [{"name": "search_live", "inputSchema": {"properties": {"query": {}}}}]}),
        ]
        client.execute_tool.return_value = {
            "text": '{"query":"x"}',
            "parsed": {"query": "x"},
        }
        mock_plan.return_value = ("worldcup-live", "search_live", {"query": "2026世界杯"}, "heuristic")

        with patch.dict(os.environ, {"MCP_GATEWAY_ENABLED": "true"}):
            get_mcp_gateway_config.cache_clear()
            payload = run_external_mcp_lookup("2026世界杯")

        self.assertEqual(client.call_gateway_tool.call_count, 2)
        client.execute_tool.assert_called_once()
        self.assertEqual(payload["server"], "worldcup-live")
        self.assertEqual(payload["tool"], "search_live")
        self.assertEqual(payload["gateway_flow"], ["list_servers", "get_server_tools", "execute_tool"])

    @patch("workflows.external_lookup.get_mcp_gateway_client")
    def test_run_external_mcp_lookup_direct_fallback_when_gateway_down(
        self,
        mock_get_client,
    ) -> None:
        from core.mcp_gateway_client import McpGatewayError

        mock_get_client.return_value.call_gateway_tool.side_effect = McpGatewayError(
            "cannot connect"
        )
        with patch.dict(os.environ, {"MCP_GATEWAY_ENABLED": "true"}):
            get_mcp_gateway_config.cache_clear()
            payload = run_external_mcp_lookup("2026世界杯主办国")

        self.assertEqual(payload["plan_method"], "direct_fallback")
        self.assertEqual(payload["server"], "worldcup-live")
        self.assertIn("mcp:direct_fallback", payload["tool_trace"])


if __name__ == "__main__":
    unittest.main()
