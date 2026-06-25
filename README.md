# worldcup-rag

## 目标架构：分层解耦 + 可插拔
┌─────────────────────────────────────────────────────────────────────────┐
│                           接入层（统一网关）                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │ 鉴权/限流    │  │ 审计日志     │  │ 安全盾牌     │               │
│  └──────────────┘  └──────────────┘  └──────────────┘               │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────┐
│                      编排层（Workflow Orchestrator）                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  工作流注册表（Workflow Registry）                           │  │
│  │  ├── SimpleQAWorkflow                                       │  │
│  │  ├── GraphRAGWorkflow                                       │  │
│  │  ├── MultiHopReasoningWorkflow                              │  │
│  │  └── CustomWorkflow ...                                    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  路由决策器（Router）：根据 Query 动态选择 Workflow          │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────┐
│                      能力层（Capability Layer）                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │
│  │ Tool Registry│  │ SubGraph     │  │ Memory       │            │
│  │ (工具注册表)   │  │ Manager      │  │ Manager      │            │
│  └──────────────┘  └──────────────┘  └──────────────┘            │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────────┐
│                      适配层（Adapter Layer）                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │PostgreSQL│  │ Neo4j    │  │ 免费API   │  │ Mock     │           │
│  │Adapter   │  │ Adapter  │  │ Adapter  │  │ Adapter  │           │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘           │
└─────────────────────────────────────────────────────────────────────┘




## 项目结构

football-rag/
├── core/
│   ├── security.py          # 入站脱敏 / SQL 校验 / 出站扫描（正则 + 熵检测）
│   ├── security_config.py   # config.yaml + SECURITY_* 环境变量
│   ├── middleware.py        # /chat 安全中间件
│   ├── query_cache.py       # L1/L2/语义缓存
│   └── memory.py            # 会话记忆
├── workflows/
│   ├── base.py              # Workflow 基类
│   ├── simple_qa.py
│   ├── complex_flow.py
│   └── gossip.py
├── workers/
│   └── post_chat_worker.py  # 延迟缓存写入 / 摘要压缩
├── benchmark/
│   ├── golden.json
│   └── benchmark.py
├── app.py                   # FastAPI 入口
├── agent.py                 # chat 入口（缓存写入前出站脱敏）
├── worker.py                # 后台 worker CLI
└── config.yaml              # cache / queue / security 配置

## Security（入站 / 出站）

`POST /chat` 请求经 `core/middleware.py`：

1. **入站**：脱敏 query/history 中的密钥、手机、邮箱等；拦截明显 SQL 注入模式
2. **出站**：扫描 `answer` 与 `sql_generated`，命中则脱敏并写 audit 日志

`agent.chat` 与 worker 缓存写入前调用同一套 `SecurityFilter.redact_chat_result`，避免缓存侧信道。

配置见 `config.yaml` → `security`，可用环境变量覆盖（改后需重启或清 `get_security_config` 缓存）：

| 环境变量 | 含义 |
|----------|------|
| `SECURITY_ENABLED` | 总开关 |
| `SECURITY_SANITIZE_INPUT` | 入站脱敏 |
| `SECURITY_SCAN_OUTPUT` | 出站扫描 |
| `SECURITY_ENTROPY_SCAN_ENABLED` | L2 高熵 token 扫描 |
| `SECURITY_ENTROPY_THRESHOLD` | 熵阈值（默认 4.2） |

不引入 Presidio；球员/球队名不会被当作 PII 脱敏。

`security.enabled=true` 时，结构化日志（`message` / `context` / `exception`）与 LangSmith trace 的 inputs/outputs 会经同一套 `sanitize_text` 规则脱敏后再落盘/上传。

## Session Memory（多轮对话）

服务端会话记忆存 Redis（`chat:session:{session_id}:*`），与 query cache（`exact:` / semantic）分离。

### 客户端用法

**推荐：只传 `session_id`（服务端 memory）**

```json
POST /chat
{
  "query": "梅西在世界杯进了几个球？",
  "session_id": "user-abc-001"
}
```

**Benchmark / 单测：只传 `history`（客户端自带上下文）**

```json
{
  "query": "...",
  "history": [{"user": "...", "assistant": "..."}]
}
```

不要同时传 `session_id` 和 `history`（会返回 400）。

`session_id` 格式：`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`

### 响应字段

| 字段 | 含义 |
|------|------|
| `memory_persisted: null` | 未传 session |
| `memory_persisted: false` | 有 session 但未写入（Redis 不可用 / 失败 / workflow 错误） |
| `memory_persisted: true` | 本轮 user+assistant 已原子写入 Redis |

有 `session_id` 时会跳过全局 query cache。

### Router 与 Memory

- 规则 router 默认开启；`ROUTER_LLM_ENABLED=true` 时，对指代/续问类短句用小模型补路由
- 所有 workflow 共享同一 Redis memory，按 `session_id` 隔离
- 目前 **仅 `simple_qa` 将 memory 注入 Agent prompt**；`complex_flow` / `gossip` 会写入 memory 供后续轮次与 router 使用

### Session 运维 API

```bash
GET    /session/{session_id}/stats
DELETE /session/{session_id}
```

### 运行测试

```bash
PYTHONPATH=. python -m unittest discover -s tests -p 'test_*.py' -v
```

CI：`.github/workflows/tests.yml`