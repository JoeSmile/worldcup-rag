import os
import json
import logging
from typing import List, Dict
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.tools import tool
from prompts import SYSTEM_PROMPT
from tools import (
    execute_sql,
    get_player_stats,
    search_players_by_name,
    semantic_search as search_worldcup_knowledge,
)

load_dotenv()
logger = logging.getLogger(__name__)

# 避免 LangSmith 未配置时启动刷屏
if os.getenv("LANGSMITH_TRACING", "").lower() == "true" and not os.getenv("LANGSMITH_API_KEY"):
    os.environ["LANGSMITH_TRACING"] = "false"


def _to_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


# 把工具包装成 LangChain 格式
@tool
def search_players(name: str) -> str:
    """按名字或中文俗称搜索球员 fact card（贝利、梅西、大罗、C罗等）。"""
    return _to_json(search_players_by_name(name))

@tool
def semantic_search(query: str) -> str:
    """语义搜索世界杯知识库，适合开放式问题和不确定该查什么表的问题。"""
    return _to_json(search_worldcup_knowledge(query))

@tool
def player_stats(name: str) -> str:
    """搜索球员世界杯生涯、进球、出场、奖项；支持中文俗称（贝利、大罗、C罗、梅西等）。"""
    return _to_json(get_player_stats(name))

@tool
def sql_query(sql: str) -> str:
    """执行只读 SQL。仅可查询 vw_player_summary、vw_match_summary、vw_team_tournament_summary 或 documents/document_chunks。"""
    return _to_json(execute_sql(sql))

# 初始化 LLM（用智谱或通义，国内直连）
llm = ChatOpenAI(
    model=os.getenv("MODEL_NAME", "qwen3-max"),
    base_url=os.getenv(
        "API_BASE",
        os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ),
    api_key=os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY"),
    temperature=0
)

# 创建 Agent
agent_executor = create_agent(
    model=llm,
    tools=[search_players, semantic_search, player_stats, sql_query],
    system_prompt=SYSTEM_PROMPT,
    debug=os.getenv("AGENT_DEBUG", "false").lower() == "true",
)

def _extract_chat_metadata(messages) -> dict:
    """从 agent 消息流提取工具调用、SQL 与 token 用量。"""
    tools_used: list[str] = []
    sql_generated = None
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0

    for msg in messages:
        for tc in getattr(msg, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if not name:
                continue
            tools_used.append(name)
            if name == "sql_query":
                args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                if isinstance(args, dict) and args.get("sql"):
                    sql_generated = args["sql"]

        usage = (getattr(msg, "response_metadata", None) or {}).get("token_usage") or {}
        total_tokens += int(usage.get("total_tokens") or 0)
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)

    return {
        "tool_name": tools_used[-1] if tools_used else None,
        "tools_used": tools_used,
        "sql_generated": sql_generated,
        "usage": {
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }


def chat(query: str, history: List[Dict] = None) -> dict:
    """聊天入口，返回答案与评测用元数据。"""
    messages = []
    if history:
        for item in history[-5:]:
            messages.append({"role": "user", "content": item["user"]})
            messages.append({"role": "assistant", "content": item["assistant"]})
    messages.append({"role": "user", "content": query})

    recursion_limit = max(int(os.getenv("AGENT_MAX_ITERATIONS", "10")) + 4, 14)
    try:
        result = agent_executor.invoke(
            {"messages": messages},
            config={"recursion_limit": recursion_limit},
        )
    except Exception as exc:
        logger.exception("agent invoke failed")
        return {
            "answer": f"抱歉，处理您的问题时出错：{exc}",
            "tool_name": None,
            "tools_used": [],
            "sql_generated": None,
            "usage": {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0},
            "error": str(exc),
        }

    agent_messages = result["messages"]
    metadata = _extract_chat_metadata(agent_messages)
    return {
        "answer": agent_messages[-1].content,
        **metadata,
    }