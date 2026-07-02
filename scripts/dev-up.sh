#!/usr/bin/env bash
# Start full local stack: infra + monitoring + MCP Gateway monorepo subproject.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_API=false
for arg in "$@"; do
  case "$arg" in
    --with-api) WITH_API=true ;;
    -h|--help)
      echo "Usage: $0 [--with-api]"
      echo "  Starts postgres, redis, prometheus, grafana, mcp-metrics (+ experimental mcp-gateway container)."
      echo "  --with-api  Also run app.py with MCP Gateway client enabled."
      exit 0
      ;;
  esac
done

export MCP_GATEWAY_ENABLED="${MCP_GATEWAY_ENABLED:-true}"
export MCP_GATEWAY_URL="${MCP_GATEWAY_URL:-http://localhost:8080/mcp}"
export MCP_GATEWAY_EMBED_PROCESS="${MCP_GATEWAY_EMBED_PROCESS:-false}"

PYTHONPATH="$ROOT" python3 - <<'PY'
from core.mcp_gateway_config import ensure_gateway_config_files
ensure_gateway_config_files()
print("gateway config ready:", "mcp/gateway/.mcp.json")
PY

echo "==> docker compose --profile mcp up -d"
docker compose --profile mcp up -d postgres redis prometheus grafana mcp-metrics mcp-gateway

GATEWAY_OK=false
for _ in 1 2 3 4 5; do
  if curl -sf --connect-timeout 2 "http://localhost:${MCP_GATEWAY_PORT:-8080}/health" >/dev/null 2>&1; then
    GATEWAY_OK=true
    break
  fi
  sleep 1
done

if [[ "$GATEWAY_OK" == "true" ]]; then
  GATEWAY_MSG="MCP Gateway HTTP: UP (localhost:${MCP_GATEWAY_PORT:-8080})"
else
  GATEWAY_MSG="MCP Gateway HTTP: DOWN (8080 无监听 — agent-mcp-gateway 当前仅 stdio，Docker 容器不会提供 HTTP)"
fi

METRICS_OK=false
if curl -sf --connect-timeout 2 "http://localhost:${MCP_METRICS_PORT:-8081}/metrics" >/dev/null 2>&1; then
  METRICS_OK=true
fi

cat <<EOF

Stack is up:
  Postgres     localhost:${POSTGRES_PORT:-5432}
  Redis        localhost:${REDIS_PORT:-6379}
  API (manual) localhost:8000
  MCP Gateway  localhost:${MCP_GATEWAY_PORT:-8080}  — ${GATEWAY_MSG}
  MCP metrics  localhost:${MCP_METRICS_PORT:-8081}/metrics  — $([[ "$METRICS_OK" == true ]] && echo UP || echo starting...)
  Prometheus   localhost:${PROMETHEUS_PORT:-9090}
  Grafana      localhost:${GRAFANA_PORT:-3000}  (admin / ${GRAFANA_ADMIN_PASSWORD:-admin})

⚠️  若 Gateway DOWN：API 启动时会打印 MCP GATEWAY DOWN；/ready 返回 degraded + warnings。
    本地可设 MCP_GATEWAY_EMBED_PROCESS=true（需 uv）或保持 dev_direct_fallback。

EOF

if [[ "$GATEWAY_OK" != "true" ]]; then
  echo "Gateway container logs (last 5 lines):"
  docker logs worldcup-rag-mcp-gateway --tail 5 2>/dev/null || true
  echo
fi

if [[ "$WITH_API" == "true" ]]; then
  echo "==> starting app.py (MCP_GATEWAY_ENABLED=$MCP_GATEWAY_ENABLED)"
  exec env MCP_GATEWAY_ENABLED="$MCP_GATEWAY_ENABLED" \
    MCP_GATEWAY_URL="$MCP_GATEWAY_URL" \
    MCP_GATEWAY_EMBED_PROCESS="$MCP_GATEWAY_EMBED_PROCESS" \
    PYTHONPATH="$ROOT" \
    python3 app.py
fi

echo "Start API:"
echo "  MCP_GATEWAY_ENABLED=true MCP_GATEWAY_URL=$MCP_GATEWAY_URL PYTHONPATH=. python3 app.py"
echo "Check readiness:"
echo "  curl -s localhost:8000/ready | python3 -m json.tool"
