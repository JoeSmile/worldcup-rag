#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export GATEWAY_MCP_CONFIG="${GATEWAY_MCP_CONFIG:-$ROOT/mcp/gateway/.mcp.json}"
export GATEWAY_RULES="${GATEWAY_RULES:-$ROOT/mcp/gateway/.mcp-gateway-rules.json}"
export GATEWAY_DEFAULT_AGENT="${GATEWAY_DEFAULT_AGENT:-worldcup-external}"
export GATEWAY_TRANSPORT="${GATEWAY_TRANSPORT:-http}"
export GATEWAY_PORT="${GATEWAY_PORT:-8080}"

if [[ ! -f "$GATEWAY_MCP_CONFIG" ]]; then
  cp "$ROOT/mcp/gateway/.mcp.json.example" "$GATEWAY_MCP_CONFIG"
fi
if [[ ! -f "$GATEWAY_RULES" ]]; then
  cp "$ROOT/mcp/gateway/.mcp-gateway-rules.json.example" "$GATEWAY_RULES"
fi

PYTHONPATH="$ROOT" python3 - <<'PY' || true
from core.mcp_gateway_config import ensure_gateway_config_files
ensure_gateway_config_files()
PY

echo "Starting MCP Gateway HTTP bridge on :$GATEWAY_PORT (stdio agent-mcp-gateway + /mcp)"
echo "Config: $GATEWAY_MCP_CONFIG"
echo "Rules:  $GATEWAY_RULES"

export GATEWAY_HOST="${GATEWAY_HOST:-0.0.0.0}"
export PYTHONPATH="$ROOT"

exec python3 "$ROOT/mcp/gateway/http_bridge.py"
