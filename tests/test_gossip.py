"""Gossip workflow smoke tests."""

from __future__ import annotations

import unittest

from workflows.base import WorkflowContext
from workflows.gossip import step_classify_topic


class GossipWorkflowTests(unittest.TestCase):
    def test_classify_topic_recognizes_gossip_keywords(self) -> None:
        ctx = WorkflowContext(query="有什么世界杯八卦？")
        step_classify_topic(ctx)
        self.assertIn("gossip", ctx.metadata["gossip_analysis"]["topics"])


if __name__ == "__main__":
    unittest.main()
