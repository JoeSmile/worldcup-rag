"""Tests for MCP Gateway config loading."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.mcp_gateway_config import ensure_gateway_config_files, get_mcp_gateway_config


class McpGatewayConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_mcp_gateway_config.cache_clear()

    def test_env_overrides_yaml(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MCP_GATEWAY_ENABLED": "true",
                "MCP_GATEWAY_URL": "http://gateway:8080/mcp",
                "MCP_GATEWAY_TIMEOUT_MS": "12000",
            },
        ):
            get_mcp_gateway_config.cache_clear()
            cfg = get_mcp_gateway_config()
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.url, "http://gateway:8080/mcp")
            self.assertEqual(cfg.timeout_ms, 12000)
            self.assertFalse(cfg.embed_gateway_process)

    def test_ensure_gateway_config_files_writes_absolute_server_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway_dir = root / "mcp/gateway"
            gateway_dir.mkdir(parents=True)
            (gateway_dir / ".mcp.json.example").write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "worldcup-live": {
                                "command": "python3",
                                "args": ["mcp/servers/worldcup_live/server.py"],
                                "env": {"PYTHONPATH": "."},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (gateway_dir / ".mcp-gateway-rules.json.example").write_text("{}", encoding="utf-8")
            server_path = root / "mcp/servers/worldcup_live/server.py"
            server_path.parent.mkdir(parents=True)
            server_path.write_text("# stub", encoding="utf-8")

            config_path = gateway_dir / ".mcp.json"
            with patch("core.mcp_gateway_config._REPO_ROOT", root):
                with patch.dict(os.environ, {"MCP_GATEWAY_ENABLED": "true"}):
                    get_mcp_gateway_config.cache_clear()
                    ensure_gateway_config_files()
            data = json.loads(config_path.read_text(encoding="utf-8"))
            args = data["mcpServers"]["worldcup-live"]["args"]
            self.assertEqual(args[0], str(server_path.resolve()))


if __name__ == "__main__":
    unittest.main()
