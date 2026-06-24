import os
import json
from typing import List, Dict
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain.tools import tool
from tools import (
    execute_sql,
    get_player_stats,
    search_players_by_name,
    semantic_search as search_worldcup_knowledge,
)

load_dotenv()

SYSTEM_PROMPT = """你是世界杯足球数据分析助手。

你可以通过工具访问本项目 PostgreSQL/pgVector 数据库中的世界杯资料：

1. search_players(name) - 搜索球员相关 fact card。
2. semantic_search(query) - 语义搜索世界杯知识库，适合开放式问题。
3. player_stats(name) - 搜索球员世界杯生涯、进球、出场、奖项等统计资料。
4. sql_query(sql) - 执行只读 SQL，用于复杂结构化分析。

优先使用工具获取事实依据，再用中文简洁回答。不要编造数据库没有返回的信息。
"""


def _to_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


# 把工具包装成 LangChain 格式
@tool
def search_players(name: str) -> str:
    """按名字搜索球员相关的世界杯 fact card。"""
    return _to_json(search_players_by_name(name))

@tool
def semantic_search(query: str) -> str:
    """语义搜索世界杯知识库，适合开放式问题和不确定该查什么表的问题。"""
    return _to_json(search_worldcup_knowledge(query))

@tool
def player_stats(name: str) -> str:
    """搜索球员世界杯生涯、进球、出场、奖项等统计 fact card。"""
    return _to_json(get_player_stats(name))

@tool
def sql_query(sql: str) -> str:
    """执行只读 SQL 查询，用于复杂结构化分析。"""
    # 安全检查：只允许 SELECT
    if not sql.strip().upper().startswith("SELECT"):
        return "错误：只允许 SELECT 查询"
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

def chat(query: str, history: List[Dict] = None):
    """聊天入口"""
    # 如果有历史对话，拼接到输入里
    messages = []
    if history:
        for item in history[-5:]:
            messages.append({"role": "user", "content": item["user"]})
            messages.append({"role": "assistant", "content": item["assistant"]})
    messages.append({"role": "user", "content": query})
    
    result = agent_executor.invoke({
        "messages": messages,
    })
    return result["messages"][-1].content