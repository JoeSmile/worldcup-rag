"""Generate LangSmith datasets script tests."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.generate_langsmith_datasets import build_datasets


class GenerateLangsmithDatasetsTests(unittest.TestCase):
    def test_build_datasets_splits_by_router(self) -> None:
        golden = json.loads(
            (Path(__file__).resolve().parents[1] / "benchmark" / "golden.json").read_text(encoding="utf-8")
        )
        datasets = build_datasets(golden)
        self.assertIn("worldcup-rag-router", datasets)
        self.assertIn("worldcup-rag-gossip", datasets)
        router = datasets["worldcup-rag-router"]["examples"][0]
        self.assertIn("query", router["inputs"])
        self.assertNotIn("graph", router["inputs"])
        gossip = datasets["worldcup-rag-gossip"]["examples"]
        self.assertGreaterEqual(len(gossip), 3)


if __name__ == "__main__":
    unittest.main()
