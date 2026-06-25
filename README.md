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
│   ├── registry.py          # ToolRegistry
│   ├── security.py          # SecurityPipeline
│   
├── workflows/
│   ├── base.py              # Workflow 基类
│   ├── simple_qa.py
│   ├── complex_flow.py
│   └── gossip.py
├── adapters/
│   ├── base.py              # Adapter 接口
│   ├── postgres_adapter.py
│   ├── neo4j_adapter.py
│   ├── free_api_adapter.py  # Travly 免费搜索
│   └── mock_adapter.py      # 开发测试用
├── subgraphs/
│   ├── manager.py           # SubGraphManager
│   └── schemas/             # 各子图 Schema 定义
├── security/
│   └── filters/             # 各种安全过滤器
├── agents/
│   ├── base.py              # Agent 基类（可替换不同 LLM）
│   └── qwen_agent.py        # Qwen 实现
├── benchmark/
│   ├── golden.json
│   └── runner.py
├── app.py                   # FastAPI 入口（极简）
└── config.yaml              # 配置文件（环境切换）

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