"""Live / out-of-corpus World Cup lookup (shared by MCP server and optional direct import)."""

from __future__ import annotations

import json
import os


def search_live(query: str) -> str:
    """Return live lookup payload. Wire brave-search in gateway for real web results."""
    text = query.strip()
    payload: dict[str, object] = {
        "query": text,
        "source": "worldcup-live",
        "mode": os.environ.get("WORLDCUP_LIVE_MODE", "stub"),
    }

    lowered = text.lower()
    if "2026" in text or "二零二六" in text:
        payload["facts"] = [
            "2026 FIFA World Cup will be hosted by the United States, Canada, and Mexico.",
            "It is the first edition with 48 teams.",
        ]
    elif any(token in lowered for token in ("host", "主办", "举办地")):
        payload["facts"] = [
            "For editions after 2022, verify hosts and schedules via an external search provider.",
        ]
    else:
        payload["facts"] = [
            "This stub answers out-of-corpus / current-event questions when MCP Gateway is enabled.",
            "Configure brave-search in mcp/gateway/.mcp.json for production web lookup.",
        ]

    return json.dumps(payload, ensure_ascii=False)
