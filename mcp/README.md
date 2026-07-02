# MCP 子项目（monorepo）

与 `workflows/`、`core/` 并列的可插拔外部能力层。

```text
mcp/
├── gateway/                 # agent-mcp-gateway 配置（.mcp.json + rules）
├── servers/                 # 自建下游 MCP Server（stdio，由 Gateway spawn）
│   ├── worldcup_live/       # 2026+ / 实时 stub
│   └── _template/           # 新 Server 模板
└── monitoring/
    └── exporter.py          # Prometheus 探针（Grafana 用）
```

## 新增 MCP Server

1. 复制 `servers/_template/` → `servers/<your_server>/`
2. 在 `gateway/.mcp.json` 的 `mcpServers` 增加条目（`command` + `args` 指向 `server.py`）
3. 在 `gateway/.mcp-gateway-rules.json` 给 `worldcup-external` 授权
4. 重启 Gateway（或热加载）；**无需改** `workflows/external_lookup.py`

## 一键启动（含 Gateway + 监控）

**在哪跑**：必须在仓库根目录 `worldcup-rag/`（不是 `mcp/` 目录）。

**前置**：Docker 已启动；`pip install -r requirements.txt`；`.env` 里配好 `API_KEY`（`external_qa` 用 LLM 时需要）。

### 方式 A：只起基础设施（推荐，两个终端）

终端 1 — 起 Docker 栈（postgres / redis / prometheus / grafana / mcp-gateway / mcp-metrics）：

```bash
cd /path/to/worldcup-rag
./scripts/dev-up.sh
# 等价：make dev-up
```

脚本结束后**会退出**，容器在后台跑。再开终端 2 起 API：

**配置 MCP（二选一，不必每次 export）**

1. **`.env`**（推荐，与 `API_KEY` 一样）：

```bash
# .env
MCP_GATEWAY_ENABLED=true
MCP_GATEWAY_URL=http://localhost:8080/mcp
MCP_GATEWAY_EMBED_PROCESS=false
```

2. **`config.yaml`** → `mcp_gateway.enabled: true`（`url` 默认同上）

然后启动（`PYTHONPATH` 仍需写在命令前，Python 不会从 `.env` 读它）：

```bash
cd /path/to/worldcup-rag
PYTHONPATH=. python3 app.py
```

API 默认：http://localhost:8000

### 方式 B：一条命令起栈 + API（单终端，前台阻塞）

```bash
cd /path/to/worldcup-rag
./scripts/dev-up.sh --with-api
# 等价：make dev-api
```

先 `docker compose up -d`，再**前台**跑 `app.py`（已设 `MCP_GATEWAY_ENABLED=true`）。`Ctrl+C` 只停 API，**不会**停 Docker 容器。

### 停栈

```bash
cd /path/to/worldcup-rag
./scripts/dev-down.sh
# 等价：make dev-down
```

### 快速验证 MCP

```bash
# 2026 → external_qa + MCP
curl -s http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"2026世界杯主办国是哪里？"}' | python3 -m json.tool

# 探针指标
curl -s http://localhost:8081/metrics | grep worldcup_mcp
```

Grafana：http://localhost:3000（admin / admin）→ **World Cup RAG** → **MCP Stack**。

### 如何知道 Gateway 是否挂了

| 入口 | 现象 |
|------|------|
| **`./scripts/dev-up.sh`** | 结束时打印 `MCP Gateway HTTP: DOWN/UP` |
| **API 启动** | 终端出现 `⚠️ MCP GATEWAY DOWN: {...}` |
| **`GET /ready`** | `"degraded": true`, `"mcp_gateway": {"status":"down", ...}` |
| **`POST /chat`（external_qa）** | `"mcp_gateway_mode": "direct_fallback"` 表示绕过了 Gateway |
| **Grafana MCP Stack** | `worldcup_mcp_gateway_up = 0`（需 mcp-metrics 容器正常） |

8080 连不上的**根因**：`agent-mcp-gateway` **M1 只有 stdio**，Docker 里进程启动后立即退出，**不会监听 8080**；`GATEWAY_TRANSPORT=http` 尚未稳定可用。

常见原因：**8080 上没有 Gateway HTTP 服务**（`agent-mcp-gateway` 的 HTTP 仍在 M2），或没跑 `./scripts/dev-up.sh`。

此时默认 **`MCP_GATEWAY_DEV_DIRECT_FALLBACK=true`**：Gateway 失败后自动直连 `mcp/servers/worldcup_live` stub，`tools_used` 会出现 `mcp:direct_fallback`（可本地验证 external_qa，但不是完整 Gateway 链路）。

要测 **完整 Gateway 链路**：

```bash
./scripts/dev-up.sh          # 确保 mcp-gateway 容器在跑
curl -s localhost:8080/health # 应通；不通则 HTTP 模式暂不可用
```

或安装 [uv](https://docs.astral.sh/uv/) 后：

```bash
MCP_GATEWAY_EMBED_PROCESS=true
PYTHONPATH=. python3 app.py
```

生产环境请设 `MCP_GATEWAY_DEV_DIRECT_FALLBACK=false`，避免绕过 Gateway。
