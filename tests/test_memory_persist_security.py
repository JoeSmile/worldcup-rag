"""Session memory persist must not store raw sensitive spans."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from workflows.base import MemoryAwareWorkflow, WorkflowContext


class _PersistProbeWorkflow(MemoryAwareWorkflow):
    def __init__(self, memory: MagicMock):
        super().__init__("test_wf", steps=[lambda ctx: ctx])
        self.memory = memory


class MemoryPersistSecurityTests(unittest.TestCase):
    def test_persist_memory_sanitizes_query_and_answer(self) -> None:
        memory = MagicMock()
        memory.available = True
        memory.add_turn.return_value = True

        workflow = _PersistProbeWorkflow(memory)
        ctx = WorkflowContext(
            query="my phone 13812345678",
            metadata={"session_id": "session-1"},
        )
        ctx.set_answer("contact 13898765432")

        workflow._persist_memory(ctx)

        memory.add_turn.assert_called_once()
        call = memory.add_turn.call_args
        session_id, user_text, assistant_text = call.args
        self.assertEqual(session_id, "session-1")
        self.assertEqual(call.kwargs.get("workflow"), "test_wf")
        self.assertNotIn("13812345678", user_text)
        self.assertNotIn("13898765432", assistant_text)
        self.assertIn("[REDACTED]", user_text)
        self.assertIn("[REDACTED]", assistant_text)


if __name__ == "__main__":
    unittest.main()
