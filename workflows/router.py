"""Route user queries to simple_qa, complex_flow, or gossip."""

from __future__ import annotations

from typing import Optional

# 闲聊 / 八卦 / 趣闻（优先级最高）
_GOSSIP_KEYWORDS = (
    "八卦",
    "绯闻",
    "趣闻",
    "轶事",
    "花边",
    "恋情",
    "传闻",
    "爆料",
    "内幕",
    "花絮",
    "瓜",
    "八卦新闻",
    "场外",
    "私生活",
    "桃色",
    "冷知识",
    "好玩",
    "有趣",
)

# 复杂统计 / 对比 / 排行
_COMPLEX_KEYWORDS = (
    "对比",
    "谁更",
    "排名",
    "名单",
    "统计",
    "合计",
    "总共",
    "一共几次",
    "分别",
    "各届",
    "最多",
    "最少",
    "榜单",
    "纪录",
    "前十",
    "前三",
    "进球最多",
    "谁进球",
    "对比分析",
    "几次",
    "多少个",
    "多少场",
    "几场",
    "小组赛",
    "哪些年份",
    "哪些年",
    "冠军次数",
)

# 「和」单独太宽，仅在对比语境下视为复杂流
_COMPLEX_WITH_AND = ("和", "与", "还是")


def route(query: str) -> str:
    """Return workflow name: gossip | complex_flow | simple_qa."""
    text = query.strip()
    if not text:
        return "simple_qa"

    if any(kw in text for kw in _GOSSIP_KEYWORDS):
        return "gossip"

    if any(kw in text for kw in _COMPLEX_KEYWORDS):
        return "complex_flow"

    if any(kw in text for kw in _COMPLEX_WITH_AND) and any(
        hint in text for hint in ("谁", "哪个", "哪支", "哪家", "更多", "更强")
    ):
        return "complex_flow"

    return "simple_qa"


class WorkflowRouter:
    """Rule-based router over registered workflows."""

    def __init__(self) -> None:
        self._workflow_names = ("gossip", "complex_flow", "simple_qa")

    def route(self, query: str) -> str:
        from workflows.registry import registry

        name = route(query)
        if registry.get(name) is None:
            return "simple_qa"
        return name

    def list_routes(self) -> dict[str, str]:
        return {
            "gossip": "足球八卦、绯闻、趣闻、花絮等闲聊",
            "complex_flow": "对比、排行、多条件统计等复杂查询",
            "simple_qa": "默认：球员数据、赛果、单一事实问答",
        }

    def run(
        self,
        query: str,
        history: Optional[list] = None,
        workflow: Optional[str] = None,
    ) -> dict:
        """Route (unless workflow forced) and execute."""
        from workflows.registry import registry

        chosen = workflow or self.route(query)
        wf = registry.get(chosen)
        if wf is None:
            available = ", ".join(registry.list_names())
            raise ValueError(f"Unknown workflow '{chosen}'. Available: {available}")

        result = wf.run(query, history=history)
        result["router_choice"] = chosen if workflow is None else workflow
        result["auto_routed"] = workflow is None
        return result


default_router = WorkflowRouter()
