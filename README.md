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


| 环境变量                            | 含义             |
| ------------------------------- | -------------- |
| `SECURITY_ENABLED`              | 总开关            |
| `SECURITY_SANITIZE_INPUT`       | 入站脱敏           |
| `SECURITY_SCAN_OUTPUT`          | 出站扫描           |
| `SECURITY_ENTROPY_SCAN_ENABLED` | L2 高熵 token 扫描 |
| `SECURITY_ENTROPY_THRESHOLD`    | 熵阈值（默认 4.2）    |


不引入 Presidio；球员/球队名不会被当作 PII 脱敏。

`security.enabled=true` 时，结构化日志（`message` / `context` / `exception`）与 LangSmith trace 的 inputs/outputs 会经同一套 `sanitize_text` 规则脱敏后再落盘/上传。

## Session Memory（多轮对话）

服务端会话记忆存 Redis（`chat:session:{session_id}:`*），与 query cache（`exact:` / semantic）分离。

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


| 字段                        | 含义                                           |
| ------------------------- | -------------------------------------------- |
| `memory_persisted: null`  | 未传 session                                   |
| `memory_persisted: false` | 有 session 但未写入（Redis 不可用 / 失败 / workflow 错误） |
| `memory_persisted: true`  | 本轮 user+assistant 已原子写入 Redis                |


有 `session_id` 时会跳过全局 query cache。

### Router 与 Memory

- 规则 router 默认开启；`ROUTER_LLM_ENABLED=true` 时，对指代/续问类短句用小模型补路由
- 所有 workflow 共享同一 Redis memory，按 `session_id` 隔离
- 目前 **仅 `simple_qa` 将 memory 注入 Agent prompt**；`complex_flow` / `gossip` 会写入 memory 供后续轮次与 router 使用
- `**complex_flow`**：LLM **迭代 replan**（每步 SQL 结果影响下一步；**最多 3 轮 replan / 3 条 SQL**）→ 总结；无 API Key 时回退**有限规则模板**（梅西/C罗对比、带位置或「最多/榜单」类排行、年份/中国队等；无匹配则 `semantic_search`）。LLM 显式 `done` 且无 SQL 时 `plan_method=replan_done`（语义总结）；无效 step / 解析失败仍走规则或语义回退。

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

## LangSmith Studio（本地）

`simple_qa` Agent 基于 LangGraph（`create_agent` → `CompiledStateGraph`）。根目录 **`langgraph.json`** 供 **LangGraph CLI** 加载，在浏览器 **LangSmith Studio** 里可视化与调试。`models.qwen3-max` 指向 DashScope OpenAI 兼容端点，密钥来自 `.env` 的 `QWEN_API_KEY`。

### 前置

```bash
cp .env.example .env
# 填写 API_KEY（DashScope）；Studio 模型配置还需 QWEN_API_KEY（可与 API_KEY 相同）
# 可选开启 LangSmith 追踪：
# LANGSMITH_TRACING=true
# LANGSMITH_API_KEY=...
```

安装 CLI（已写入 `requirements.txt`）：

```bash
pip install -r requirements.txt
```

### 启动本地 Agent Server + Studio

```bash
# 仓库根目录
langgraph dev -c langgraph.json
```

默认 API：`http://127.0.0.1:2024`  
Studio UI：`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`

Safari 若无法连 localhost，可加隧道：

```bash
langgraph dev --tunnel
```

在 Studio 中选择图 **`worldcup_chat`**（Dataset Evaluate 推荐）、**`simple_qa`**、**`complex_flow`** 或 **`gossip`**。

| 图 | 用途 | 典型输入 |
|----|------|----------|
| `worldcup_chat` | 与生产 `agent.chat` 一致；按 `inputs.graph` 分流 | `{"query": "...", "graph": "simple_qa"}` |
| `simple_qa` / `complex_flow` / `gossip` | 单 workflow 调试（step 可视化） | `{"query": "..."}` |

`simple_qa` 也支持 `{"messages": [...]}`。生产 API（`agent.chat`）会自动路由到 `simple_qa` / `complex_flow` / `gossip`。

**Studio 根 Run 统一输出**（四条图 + `worldcup_chat` 一致，便于 LangSmith Evaluator）：

`query`, `answer`, `workflow`, `graph`, `tools_used`, `tool_name`, `error`

说明：`complex_flow` / `gossip` 在 Studio 中按 step 节点可视化；生产 Chat 仍走 `app.py` / `agent.chat`（含 router 自动选路）。内部调试字段（如 gossip `tools_trace`）仅在 `metadata` 中，不进入 `tools_used`。

**Evaluate 注意**：

- 批量评估推荐 Target **`worldcup_chat`** 或 `scripts/langsmith_eval_target.run_agent`
- 单图 Studio Target（`simple_qa` / `gossip` / `complex_flow`）经 `finalize_output` 后同样绑 **`output.answer`**
- Trace 自动化：`graph` → `output.workflow`；Dataset 实验：`graph` → `input.graph`

## LangSmith Evaluate（Dataset）

```bash
# 1. 上传 Dataset（需 LANGSMITH_API_KEY）
python scripts/upload_langsmith_dataset.py
# 全量替换同名 Dataset：python scripts/upload_langsmith_dataset.py --replace

# 2. 跑实验（Target + heuristic evaluator）
python scripts/run_langsmith_evaluate.py
```

- Dataset 定义：`benchmark/langsmith_dataset.json`（混排 `graph`）或 `benchmark/langsmith_datasets/*.json`（按 workflow 拆分）
- 从 golden 重新生成：`python scripts/generate_langsmith_datasets.py`
- 复用同名 Dataset 时按 `inputs` 去重追加；**不会更新已有 example 的 reference**，改 reference 请 `--replace` 或手动删 Dataset
- 脚本内置 `reference_overlap` 仅作 smoke；正式评估建议在 UI 加 LLM-as-judge 或人工 review

Evaluator UI 路径映射：

| 变量 | 路径 |
|------|------|
| userQuestion | `input.query` |
| graph (trace) | `output.workflow` |
| graph (dataset) | `input.graph` |
| referenceOutput | `referenceOutput.reference` |
| assistantAnswer | `output.answer` |

## Monitoring（Prometheus + Grafana）

指标由 `prometheus_client` 暴露在 `GET /metrics`：


| 指标                               | 含义                                                                        |
| -------------------------------- | ------------------------------------------------------------------------- |
| `worldcup_chat_requests_total`   | Chat 请求数（workflow / status / cache_hit）                                   |
| `worldcup_chat_duration_seconds` | Chat 延迟直方图                                                                |
| `worldcup_cache_lookup_total`    | 缓存查找（l1 / l2 / semantic / miss）                                           |
| `http_requests_total`            | 全路由 HTTP 计数                                                               |
| `worldcup_llm_tokens_total`      | LLM token（仅 `prompt_tokens` / `completion_tokens`；不含 `total_tokens` 重复计数） |


**配置**（`config.yaml` → `metrics`，或环境变量）：


| 键 / 环境变量                                      | 默认     | 含义                           |
| --------------------------------------------- | ------ | ---------------------------- |
| `enabled` / `METRICS_ENABLED`                 | `true` | 关闭后 `/metrics` 返回 404，且不记录指标 |
| `public_endpoint` / `METRICS_PUBLIC_ENDPOINT` | `true` | `false` 时需 Bearer token      |
| `auth_token` / `METRICS_AUTH_TOKEN`           | —      | 与 `public_endpoint=false` 配合 |


**多 worker**（`uvicorn app:app --workers N`）：设置 `PROMETHEUS_MULTIPROC_DIR`（可写目录）。**仅在进程启动前清空一次**该目录（Docker 镜像 `ENTRYPOINT` 已处理；宿主机可先执行 `./scripts/clear-prometheus-multiproc.sh`，或 `rm -f "$PROMETHEUS_MULTIPROC_DIR"/`*），不要在每个 worker 的 `startup` 里清空，否则多 worker 会互相删指标文件。

`POST /chat` 的 `worldcup_chat_duration_seconds` 在 API 层记录，包含 `enrich_chat_result` 等后处理时间，略长于纯 workflow 耗时。

配置目录：`monitoring/`（`prometheus.yml`、Grafana provisioning、dashboard JSON、`k8s/` 示例）。

**本地（API 跑在宿主机，Prometheus 在 Docker）**

```bash
# 1. 启动 API
PYTHONPATH=. python3 app.py

# 2. 启动全部服务（含 Prometheus + Grafana）
docker compose up -d

# 或只起监控栈（API 仍需在宿主机运行）
docker compose up -d prometheus grafana

# 3. 打开 Grafana http://localhost:3000 （默认 admin / admin）
#    数据源已指向 Prometheus；Dashboard「World Cup RAG」自动加载
```

`monitoring/prometheus.yml` 默认 scrape `host.docker.internal:8000`（Docker Desktop Mac/Windows）。Linux 需 compose 里 `extra_hosts: host-gateway`（已配置），或通过环境变量覆盖：

```bash
export PROMETHEUS_SCRAPE_TARGET=172.17.0.1:8000   # 仅当 host.docker.internal 不可用时
docker compose up -d prometheus grafana
```

**K8s**：对 Pod 加注解 `prometheus.io/scrape=true`、`prometheus.io/path=/metrics`、`prometheus.io/port=8000`，或配置 `ServiceMonitor`（示例见 `monitoring/k8s/`）；生产环境建议 `METRICS_PUBLIC_ENDPOINT=false` 并配置 scrape bearer token。

Prometheus UI：`http://localhost:9090` → Status → Targets 应显示 `worldcup-rag-api` 为 UP。

## 本地 Kubernetes 测试

清单在 `k8s/local/`（Postgres + Redis Stack + API + Worker + Prometheus + Grafana）。支持 **kind**、**minikube**、**Docker Desktop Kubernetes**。

### 前置

1. 安装 `kubectl`、`docker`
2. 启用本地集群（任选其一）：
  - **Docker Desktop**：Settings → Kubernetes → Enable
  - **kind**：`./k8s/local/kind-create.sh`
  - **minikube**：`minikube start`
3. 配置密钥：`cp .env.example .env`，填写 `API_KEY`（DashScope）

### 一键部署

```bash
./k8s/local/deploy.sh
```

脚本会：构建镜像 `worldcup-rag:local`、载入 kind/minikube（如有）、从根目录 `config.yaml` 创建应用 ConfigMap、创建 Secret、`kubectl apply -k k8s/local`。

### 访问

```bash
# API
kubectl port-forward -n worldcup-rag svc/worldcup-rag-api 8000:8000
curl http://localhost:8000/health

# Prometheus / Grafana
kubectl port-forward -n worldcup-rag svc/prometheus 9090:9090
kubectl port-forward -n worldcup-rag svc/grafana 3000:3000   # admin / admin
```

### 数据与 Chat

- Postgres 使用 `emptyDir`，重启 Pod 数据会丢；仅适合联调。
- `/ready` 只需 `SELECT 1`；**真正 Chat 需要 schema + 业务数据**（视图如 `vw_player_summary`、`document_chunks` 向量列）。
- **最短本地灌数路径**（与 docker compose 共用同一 `PG_`*）：

```bash
docker compose up -d postgres redis
# 若 PG 为空：在 memoryOS 同源仓库跑迁移与世界杯 ETL，或把已有 memoryos 库指向 .env 中的 PG_*
# 详见 etl/data/bronze/worldcup/README.md（CSV → run.py → fact_cards → validate）
# 连通性自检：
PYTHONPATH=. python3 -c "from tools import execute_sql; print(execute_sql('SELECT 1'))"
```

- Worker 处理延迟缓存写入与 session 摘要，建议与 API 一起部署（manifest 已包含）。

### 清理

```bash
./k8s/local/undeploy.sh
kind delete cluster --name worldcup-rag   # 若用 kind
```

