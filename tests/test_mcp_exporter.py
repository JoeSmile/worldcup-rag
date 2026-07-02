"""Tests for MCP stack Prometheus exporter."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_exporter_module():
    path = Path(__file__).resolve().parents[1] / "mcp/monitoring/exporter.py"
    spec = importlib.util.spec_from_file_location("mcp_stack_exporter", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class McpExporterTests(unittest.TestCase):
    def test_load_configured_servers(self) -> None:
        exporter = _load_exporter_module()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / ".mcp.json"
            config_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "worldcup-live": {},
                            "brave-search": {},
                        }
                    }
                ),
                encoding="utf-8",
            )
            original = exporter._GATEWAY_CONFIG
            try:
                exporter._GATEWAY_CONFIG = config_path
                names = exporter._load_configured_servers()
            finally:
                exporter._GATEWAY_CONFIG = original
            self.assertEqual(names, ["brave-search", "worldcup-live"])


if __name__ == "__main__":
    unittest.main()
