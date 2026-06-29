#!/usr/bin/env python3
"""Upload benchmark/langsmith_dataset.json to LangSmith.

Requires LANGSMITH_API_KEY (and optional LANGSMITH_PROJECT in .env).

Re-upload behavior:
  - Default: reuse dataset by name; skip examples with identical inputs (no output upsert).
  - --replace: delete existing dataset with the same name, then create fresh.

Usage:
  python scripts/upload_langsmith_dataset.py
  python scripts/upload_langsmith_dataset.py --replace
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "benchmark" / "langsmith_dataset.json"


def _find_dataset_by_name(client, name: str):
    for dataset in client.list_datasets(dataset_name=name):
        if dataset.name == name:
            return dataset
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload LangSmith dataset from JSON")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH, help="Dataset JSON path")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing dataset with the same name before creating a new one",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    if not args.path.is_file():
        print(f"File not found: {args.path}", file=sys.stderr)
        return 1

    with args.path.open(encoding="utf-8") as file:
        payload = json.load(file)

    examples = payload.get("examples") or []
    if not examples:
        print("No examples in dataset file", file=sys.stderr)
        return 1

    try:
        from langsmith import Client
    except ImportError:
        print("langsmith package not installed (install langchain / langsmith)", file=sys.stderr)
        return 1

    client = Client()
    name = payload.get("dataset_name") or "worldcup-rag-studio"
    description = payload.get("description") or ""

    existing = _find_dataset_by_name(client, name)
    if existing and args.replace:
        client.delete_dataset(dataset_id=existing.id)
        existing = None
        print(f"Deleted existing dataset '{name}'")

    if existing:
        dataset = existing
        print(f"Reusing existing dataset '{name}' (id={dataset.id}); skipping duplicate inputs")
    else:
        dataset = client.create_dataset(dataset_name=name, description=description)
        print(f"Created dataset '{name}'")

    existing_input_keys: set[str] = set()
    if existing:
        for ex in client.list_examples(dataset_id=dataset.id):
            existing_input_keys.add(json.dumps(ex.inputs or {}, sort_keys=True, ensure_ascii=False))

    uploaded = 0
    skipped = 0
    for item in examples:
        inputs = item.get("inputs") or {}
        input_key = json.dumps(inputs, sort_keys=True, ensure_ascii=False)
        if input_key in existing_input_keys:
            skipped += 1
            continue
        client.create_example(
            inputs=inputs,
            outputs=item.get("outputs"),
            metadata=item.get("metadata"),
            dataset_id=dataset.id,
        )
        existing_input_keys.add(input_key)
        uploaded += 1

    print(f"Uploaded {uploaded} examples to '{name}' (skipped {skipped} duplicates)")
    print(f"Dataset ID: {dataset.id}")
    print(f"Open: https://smith.langchain.com/datasets/{dataset.id}")
    paths = payload.get("evaluator_paths_langsmith_ui") or payload.get("evaluator_paths") or {}
    if paths:
        print()
        print("LangSmith Evaluator UI — map template variables:")
        ui_map = {
            "userQuestion": "input.query",
            "graph (optional)": "input.graph",
            "referenceOutput": "referenceOutput.reference",
            "assistantAnswer": "output.answer (Run; gossip/complex_flow: output.answer)",
        }
        for label, path in ui_map.items():
            print(f"  {label} → {path}")
        notes = paths.get("notes")
        if isinstance(notes, list):
            for note in notes:
                print(f"  • {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
