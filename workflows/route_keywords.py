"""Shared route keyword constants for router and workflows."""

from __future__ import annotations

import re

GOSSIP_KEYWORDS = (
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
    "故事",
    "女朋友",
    "离婚",
)

FUN_KEYWORDS = ("有趣", "好玩", "冷知识", "你知道吗", "发生过什么", "难忘", "经典瞬间")

EXTERNAL_LOOKUP_KEYWORDS = (
    "2026",
    "本届",
    "当前世界杯",
    "实时",
    "最新赛程",
    "现在进行",
    "正在进行",
    "今年世界杯",
    "明年世界杯",
)

COMPLEX_KEYWORDS = (
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

COMPLEX_WITH_AND = ("和", "与", "还是")

AMBIGUOUS_PRONOUNS = (
    "他",
    "她",
    "它",
    "这个",
    "那个",
    "还有",
    "继续",
    "呢",
    "再说",
    "同样",
    "刚才",
    "上面",
)

_PLAYER_STAT_HINTS = (
    "进球",
    "进了",
    "出场",
    "位置",
    "奖项",
    "金球",
    "金手套",
    "生涯",
    "总共",
    "一共",
    "几届",
    "哪些年",
    "哪几届",
    "个人",
)

_COMPARE_HINTS = ("对比", "谁更", "谁进球更多", " vs ", "VS")

_CHAMPIONSHIP_COUNT_HINTS = ("几次", "哪些年", "哪些年份", "哪几届")


def is_player_compare(query: str) -> bool:
    """Two known players compared → simple_qa (player_stats or one sql_query)."""
    text = query.strip()
    if not any(kw in text for kw in COMPLEX_WITH_AND):
        return False
    if not any(kw in text for kw in ("谁", "更多", "更强", "进球", "哪个")):
        return False

    from tools import resolve_player_id

    parts = re.split(r"[和与]|还是", text)
    resolved = sum(1 for part in parts if resolve_player_id(part.strip()))
    return resolved >= 2


def prefers_simple_qa(query: str) -> bool:
    """Prefer simple_qa for single-entity stats misclassified by broad complex keywords."""
    text = query.strip()
    if not text:
        return False

    if is_player_compare(text):
        return True

    if "一共" in text and any(kw in text for kw in _CHAMPIONSHIP_COUNT_HINTS):
        return True

    if any(kw in text for kw in _COMPARE_HINTS):
        return False
    if any(kw in text for kw in COMPLEX_WITH_AND) and any(
        hint in text for hint in ("谁", "哪个", "哪支", "哪家", "更多", "更强")
    ):
        return False

    from tools import resolve_player_id

    if resolve_player_id(text) and any(kw in text for kw in _PLAYER_STAT_HINTS):
        return True

    if re.search(r"20\d{2}", text) and any(
        kw in text for kw in ("决赛", "半决赛", "季军赛")
    ):
        if any(kw in text for kw in ("进球", "多少个", "几个", "多少球")):
            return True

    if ("女足" in text or "男足" in text) and "进球最多" in text:
        return True

    return False
