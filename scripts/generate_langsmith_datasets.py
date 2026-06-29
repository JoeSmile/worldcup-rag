#!/usr/bin/env python3
"""Generate LangSmith dataset JSON files from benchmark/golden.json.

Usage (repo root):
  python scripts/generate_langsmith_datasets.py
  python scripts/generate_langsmith_datasets.py --out-dir benchmark/langsmith_datasets
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = ROOT / "benchmark" / "golden.json"
DEFAULT_OUT = ROOT / "benchmark" / "langsmith_datasets"

GOSSIP_ONLY_QUESTIONS = [
    {
        "category": "八卦趣闻",
        "question": "有什么世界杯八卦或场外趣闻可以聊聊？",
        "reference": "基于知识库检索的公开世界杯花絮/轶事片段；不编造未经证实的绯闻。",
        "expected_tool": "semantic_search",
    },
    {
        "category": "身份寒暄",
        "question": "你谁啊",
        "reference": "我是世界杯足球问答助手，可回答赛果、球员数据与轻松闲聊类问题。",
    },
    {
        "category": "寒暄",
        "question": "你好",
        "reference": "简短寒暄并引导聊世界杯相关话题。",
    },
]


def _reference_hint(case: dict[str, Any]) -> str:
    parts: list[str] = []
    for keyword in case.get("expected_answer_contains_all") or []:
        parts.append(f"应包含「{keyword}」")
    for group in case.get("expected_answer_groups") or []:
        parts.append(f"应包含其一：{' / '.join(group)}")
    for keyword in case.get("expected_answer_contains") or []:
        parts.append(f"可含：{keyword}")
    tool = case.get("expected_tool") or (case.get("expected_tools") or [None])[0]
    if tool:
        parts.append(f"期望工具：{tool}")
    return "；".join(parts) if parts else case.get("category", "")


def _example_from_golden(case: dict[str, Any], *, graph: str | None = None) -> dict[str, Any]:
    inputs: dict[str, Any] = {"query": case["question"]}
    if graph:
        inputs["graph"] = graph
    metadata: dict[str, Any] = {"category": case.get("category")}
    if case.get("expected_tool"):
        metadata["expected_tool"] = case["expected_tool"]
    if case.get("expected_tools"):
        metadata["expected_tools"] = case["expected_tools"]
    return {
        "inputs": inputs,
        "outputs": {"reference": _reference_hint(case)},
        "metadata": metadata,
    }


def _route_workflow(question: str) -> str:
    from workflows.router import route

    return route(question)


def build_datasets(golden: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    router_examples = [_example_from_golden(case) for case in golden]
    by_workflow: dict[str, list[dict[str, Any]]] = {
        "simple_qa": [],
        "complex_flow": [],
        "gossip": [],
    }
    mixed_examples: list[dict[str, Any]] = []

    for case in golden:
        workflow = _route_workflow(case["question"])
        by_workflow.setdefault(workflow, []).append(_example_from_golden(case))
        mixed_examples.append(_example_from_golden(case, graph=workflow))

    for item in GOSSIP_ONLY_QUESTIONS:
        by_workflow["gossip"].append(
            {
                "inputs": {"query": item["question"]},
                "outputs": {"reference": item["reference"]},
                "metadata": {
                    "category": item["category"],
                    **({"expected_tool": item["expected_tool"]} if item.get("expected_tool") else {}),
                },
            }
        )

    return {
        "worldcup-rag-router": {
            "dataset_name": "worldcup-rag-router",
            "description": "Auto-route like POST /chat. inputs.query only; Target: worldcup_chat.",
            "examples": router_examples,
        },
        "worldcup-rag-simple-qa": {
            "dataset_name": "worldcup-rag-simple-qa",
            "description": "simple_qa only. inputs.query; Target: simple_qa graph.",
            "examples": by_workflow.get("simple_qa", []),
        },
        "worldcup-rag-complex-flow": {
            "dataset_name": "worldcup-rag-complex-flow",
            "description": "complex_flow only. inputs.query; Target: complex_flow graph.",
            "examples": by_workflow.get("complex_flow", []),
        },
        "worldcup-rag-gossip": {
            "dataset_name": "worldcup-rag-gossip",
            "description": "gossip only. inputs.query; Target: gossip graph.",
            "examples": by_workflow.get("gossip", []),
        },
        "worldcup-rag-studio": {
            "dataset_name": "worldcup-rag-studio",
            "description": "Mixed dataset with inputs.graph per routed workflow. Target: worldcup_chat.",
            "examples": mixed_examples,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate LangSmith dataset JSON from golden.json")
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.golden.is_file():
        print(f"Golden file not found: {args.golden}", file=__import__("sys").stderr)
        return 1

    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    datasets = build_datasets(golden)
    for name, payload in datasets.items():
        path = args.out_dir / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {path} ({len(payload['examples'])} examples)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
