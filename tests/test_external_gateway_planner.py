"""Tests for gateway server/tool planner."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from workflows.external_gateway_planner import (
    normalize_servers,
    normalize_tools,
    plan_gateway_invocation,
)


class ExternalGatewayPlannerTests(unittest.TestCase):
    def test_normalize_servers_from_list(self) -> None:
        servers = normalize_servers([{"name": "worldcup-live", "description": "live"}])
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["name"], "worldcup-live")

    def test_normalize_tools_from_dict(self) -> None:
        tools = normalize_tools({"tools": [{"name": "search_live"}]})
        self.assertEqual(tools[0]["name"], "search_live")

    @patch("workflows.external_gateway_planner.settings")
    def test_heuristic_plan_prefers_live_server(self, mock_settings) -> None:
        mock_settings.llm_api_key = None
        servers = [
            {"name": "brave-search", "description": "web"},
            {"name": "worldcup-live", "description": "live lookup"},
        ]
        tools_by_server = {
            "worldcup-live": [{"name": "search_live", "inputSchema": {"properties": {"query": {}}}}],
            "brave-search": [{"name": "brave_web_search", "inputSchema": {"properties": {"query": {}}}}],
        }
        server, tool, args, method = plan_gateway_invocation(
            "2026世界杯主办国",
            servers,
            tools_by_server,
        )
        self.assertEqual(server, "worldcup-live")
        self.assertEqual(tool, "search_live")
        self.assertEqual(args["query"], "2026世界杯主办国")
        self.assertEqual(method, "heuristic")


if __name__ == "__main__":
    unittest.main()
