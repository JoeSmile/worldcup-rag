"""Unit tests for session memory and router helpers."""

from __future__ import annotations

import unittest

from core.memory import _estimate_message_tokens, _filter_for_workflow
from core.memory_status import resolve_memory_persisted
from core.session_id import validate_session_id
from workflows.llm_router import _parse_router_response, has_strong_rule_signal, is_ambiguous
from workflows.router import WorkflowRouter, route


class SessionIdValidationTests(unittest.TestCase):
    def test_valid_session_id(self):
        self.assertEqual(validate_session_id("user-abc_001"), "user-abc_001")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate_session_id("")

    def test_rejects_unsafe_chars(self):
        with self.assertRaises(ValueError):
            validate_session_id("bad:id*")


class RouterKeywordTests(unittest.TestCase):
    def test_route_gossip(self):
        self.assertEqual(route("有什么八卦"), "gossip")

    def test_route_complex(self):
        self.assertEqual(route("梅西和C罗谁进球更多"), "complex_flow")

    def test_route_simple(self):
        self.assertEqual(route("梅西在世界杯进了几个球"), "simple_qa")

    def test_route_benchmark_failures_to_simple_qa(self):
        cases = [
            "2022年世界杯决赛一共有多少个进球？",
            "大罗（巴西罗纳尔多）世界杯生涯总共进了几个球？",
            "中国女足在世界杯进球最多的球员是谁？进了几个球？",
        ]
        for question in cases:
            with self.subTest(question=question):
                self.assertEqual(route(question), "simple_qa")


class AmbiguousRouterTests(unittest.TestCase):
    def test_not_ambiguous_without_history(self):
        self.assertFalse(is_ambiguous("他呢？", has_session_history=False))

    def test_ambiguous_with_pronoun(self):
        self.assertTrue(is_ambiguous("他呢？", has_session_history=True))

    def test_not_ambiguous_long_factual_query(self):
        self.assertFalse(
            is_ambiguous("梅西在世界杯进了几个球？", has_session_history=True)
        )

    def test_strong_signal_skips_llm_path(self):
        self.assertTrue(has_strong_rule_signal("有什么八卦"))


class RouterParseTests(unittest.TestCase):
    def test_parse_valid_json(self):
        decision = _parse_router_response(
            '{"workflow":"complex_flow","confidence":0.91,"reason":"对比问题"}'
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.workflow, "complex_flow")
        self.assertAlmostEqual(decision.confidence, 0.91)

    def test_parse_invalid_workflow(self):
        self.assertIsNone(
            _parse_router_response('{"workflow":"unknown","confidence":0.9,"reason":"x"}')
        )


class RouterIntegrationTests(unittest.TestCase):
    def test_rule_path_without_session(self):
        router = WorkflowRouter()
        chosen, meta = router.route("梅西在世界杯进了几个球")
        self.assertEqual(chosen, "simple_qa")
        self.assertEqual(meta["method"], "rule")

    def test_ambiguous_with_history_uses_rule_when_llm_disabled(self):
        router = WorkflowRouter()
        memory_ctx = {
            "recent": [
                {"role": "user", "content": "梅西进了几个球", "workflow": "simple_qa"},
                {"role": "assistant", "content": "13个", "workflow": "simple_qa"},
            ],
            "summary": "",
        }

        class _MemoryStub:
            available = True

            def get_router_context(self, _session_id):
                return memory_ctx

        router._memory = _MemoryStub()
        chosen, meta = router.route("他呢？", session_id="user-1")
        self.assertEqual(chosen, "simple_qa")
        self.assertIn(meta["method"], {"rule", "rule_fallback"})


class MemoryPersistedIntegrationTests(unittest.TestCase):
    def test_resolve_after_failed_persist(self):
        self.assertFalse(resolve_memory_persisted("sess-1", persisted=False))

    def test_resolve_when_not_applicable(self):
        self.assertIsNone(resolve_memory_persisted(None, persisted=False))


class MemoryFilterTests(unittest.TestCase):
    def test_complex_flow_filters_gossip_assistant(self):
        recent = [
            {"role": "user", "content": "梅西进了几个球", "workflow": "simple_qa"},
            {"role": "assistant", "content": "13个", "workflow": "simple_qa"},
            {"role": "user", "content": "八卦呢", "workflow": "gossip"},
            {"role": "assistant", "content": "闲聊回复", "workflow": "gossip"},
        ]
        filtered = _filter_for_workflow(recent, "complex_flow")
        self.assertTrue(
            all(m.get("workflow") != "gossip" for m in filtered if m["role"] == "assistant")
        )

    def test_estimate_message_tokens_fallback(self):
        self.assertGreater(_estimate_message_tokens({}, content="梅西"), 0)


if __name__ == "__main__":
    unittest.main()
