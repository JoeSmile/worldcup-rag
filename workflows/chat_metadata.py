"""Extract tool usage and token stats from LangChain agent message lists."""

from __future__ import annotations

from typing import Any


def extract_chat_metadata(messages: list[Any]) -> dict[str, Any]:
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
