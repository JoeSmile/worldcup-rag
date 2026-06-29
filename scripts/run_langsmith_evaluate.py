#!/usr/bin/env python3
"""Run LangSmith Evaluate with scripts/langsmith_eval_target.run_agent.

Usage (from repo root, .env with LANGSMITH_API_KEY):
  python scripts/run_langsmith_evaluate.py
  python scripts/run_langsmith_evaluate.py --dataset worldcup-rag-studio

Heuristic `reference_overlap` is for smoke runs; add LLM-as-judge in LangSmith UI for production evals.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LangSmith evaluate experiment")
    parser.add_argument(
        "--dataset",
        default="worldcup-rag-studio",
        help="LangSmith dataset name",
    )
    parser.add_argument(
        "--prefix",
        default="worldcup-rag",
        help="Experiment name prefix",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts"))

    try:
        from langsmith import evaluate
    except ImportError:
        print("langsmith package required", file=sys.stderr)
        return 1

    from langsmith_eval_target import run_agent
    from langsmith_evaluators import reference_overlap

    results = evaluate(
        run_agent,
        data=args.dataset,
        experiment_prefix=args.prefix,
        evaluators=[reference_overlap],
        max_concurrency=1,
    )
    print(f"Experiment finished: {results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
