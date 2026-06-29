"""Metrics endpoint and chat/cache counters."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from core.metrics import (
    CACHE_LOOKUP_TOTAL,
    CHAT_REQUESTS_TOTAL,
    LLM_TOKEN_CONSUMPTION_TOTAL,
    authorize_metrics_request,
    chat_status_from_result,
    init_prometheus_multiproc,
    metrics_enabled,
    metrics_middleware,
    metrics_response,
    normalize_http_path,
    record_cache_lookup,
    record_chat_result,
    _resolve_usage_model,
)
from core.metrics_config import AppMetricsConfig, MetricsConfig, get_metrics_config


class MetricsTests(unittest.TestCase):
    def test_normalize_session_path(self) -> None:
        self.assertEqual(normalize_http_path("/session/abc-123/stats"), "/session/{session_id}")

    def test_chat_status_from_error_payload(self) -> None:
        self.assertEqual(chat_status_from_result({"workflow": "simple_qa", "error": "failed"}), "error")
        self.assertEqual(chat_status_from_result({"workflow": "simple_qa"}), "ok")
        self.assertEqual(chat_status_from_result(None), "error")

    def test_record_chat_error_payload_uses_error_status(self) -> None:
        before = CHAT_REQUESTS_TOTAL.labels(workflow="simple_qa", status="error", cache_hit="false")._value.get()
        record_chat_result({"workflow": "simple_qa", "error": "tool failed"}, 0.5)
        self.assertEqual(
            CHAT_REQUESTS_TOTAL.labels(workflow="simple_qa", status="error", cache_hit="false")._value.get(),
            before + 1,
        )

    def test_record_chat_and_cache_counters(self) -> None:
        before_chat = CHAT_REQUESTS_TOTAL.labels(workflow="simple_qa", status="ok", cache_hit="false")._value.get()
        before_cache = CACHE_LOOKUP_TOTAL.labels(layer="l1")._value.get()
        before_prompt = LLM_TOKEN_CONSUMPTION_TOTAL.labels(model="qwen-plus", type="prompt_tokens")._value.get()
        before_completion = LLM_TOKEN_CONSUMPTION_TOTAL.labels(model="qwen-plus", type="completion_tokens")._value.get()
        before_total = LLM_TOKEN_CONSUMPTION_TOTAL.labels(model="qwen-plus", type="total_tokens")._value.get()

        record_chat_result(
            {
                "workflow": "simple_qa",
                "model": "qwen-plus",
                "cache_hit": False,
                "cache_layer": None,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            1.2,
        )
        record_cache_lookup("l1")

        self.assertEqual(
            CHAT_REQUESTS_TOTAL.labels(workflow="simple_qa", status="ok", cache_hit="false")._value.get(),
            before_chat + 1,
        )
        self.assertEqual(CACHE_LOOKUP_TOTAL.labels(layer="l1")._value.get(), before_cache + 1)
        self.assertEqual(
            LLM_TOKEN_CONSUMPTION_TOTAL.labels(model="qwen-plus", type="prompt_tokens")._value.get(),
            before_prompt + 10,
        )
        self.assertEqual(
            LLM_TOKEN_CONSUMPTION_TOTAL.labels(model="qwen-plus", type="completion_tokens")._value.get(),
            before_completion + 5,
        )
        self.assertEqual(
            LLM_TOKEN_CONSUMPTION_TOTAL.labels(model="qwen-plus", type="total_tokens")._value.get(),
            before_total,
        )

    def test_resolve_usage_model_complex_flow_without_payload_model(self) -> None:
        with patch("core.config.get_settings") as mock_get_settings:
            mock_get_settings.return_value.model_name = "qwen3-max"
            mock_get_settings.return_value.resolved_complex_flow_model_name = "qwen-turbo"
            self.assertEqual(
                _resolve_usage_model({"workflow": "complex_flow", "usage": {}}),
                "qwen-turbo",
            )

    def test_record_chat_complex_flow_tokens_use_complex_flow_model(self) -> None:
        before = LLM_TOKEN_CONSUMPTION_TOTAL.labels(model="qwen-turbo", type="prompt_tokens")._value.get()
        record_chat_result(
            {
                "workflow": "complex_flow",
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
            0.5,
        )
        self.assertEqual(
            LLM_TOKEN_CONSUMPTION_TOTAL.labels(model="qwen-turbo", type="prompt_tokens")._value.get(),
            before + 3,
        )

    def test_init_prometheus_multiproc_clears_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            stale = os.path.join(tmpdir, "stale.db")
            with open(stale, "w", encoding="utf-8") as file:
                file.write("old")
            with patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": tmpdir}):
                init_prometheus_multiproc()
            self.assertFalse(os.path.exists(stale))

    def test_metrics_endpoint_returns_prometheus_format(self) -> None:
        app = FastAPI()
        app.middleware("http")(lambda req, call_next: metrics_middleware(req, call_next))

        @app.get("/metrics")
        def metrics():
            return metrics_response()

        @app.get("/health")
        def health():
            return {"status": "ok"}

        client = TestClient(app)
        response = client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.headers.get("content-type", ""))
        self.assertIn("worldcup_chat_requests_total", response.text)

    def test_metrics_app_endpoint_disabled_and_auth(self) -> None:
        def metrics_handler(http_request: Request):
            if not metrics_enabled():
                raise HTTPException(status_code=404, detail="metrics disabled")
            if not authorize_metrics_request(http_request):
                raise HTTPException(status_code=403, detail="metrics endpoint requires authorization")
            return metrics_response()

        app = FastAPI()
        app.get("/metrics")(metrics_handler)
        client = TestClient(app)

        disabled = AppMetricsConfig(metrics=MetricsConfig(enabled=False))
        with patch("core.metrics.get_metrics_config", return_value=disabled):
            self.assertEqual(client.get("/metrics").status_code, 404)

        secured = AppMetricsConfig(
            metrics=MetricsConfig(enabled=True, public_endpoint=False, auth_token="secret-token"),
        )
        with patch("core.metrics.get_metrics_config", return_value=secured):
            self.assertEqual(client.get("/metrics").status_code, 403)
            ok = client.get("/metrics", headers={"Authorization": "Bearer secret-token"})
            self.assertEqual(ok.status_code, 200)

    def test_metrics_disabled_skips_recording(self) -> None:
        disabled = AppMetricsConfig(metrics=MetricsConfig(enabled=False))
        with patch("core.metrics.get_metrics_config", return_value=disabled):
            self.assertFalse(metrics_enabled())
            before = CHAT_REQUESTS_TOTAL.labels(workflow="simple_qa", status="ok", cache_hit="false")._value.get()
            record_chat_result({"workflow": "simple_qa"}, 0.1)
            self.assertEqual(
                CHAT_REQUESTS_TOTAL.labels(workflow="simple_qa", status="ok", cache_hit="false")._value.get(),
                before,
            )

    def test_metrics_auth_requires_bearer_when_not_public(self) -> None:
        cfg = AppMetricsConfig(
            metrics=MetricsConfig(enabled=True, public_endpoint=False, auth_token="secret-token"),
        )
        with patch("core.metrics.get_metrics_config", return_value=cfg):
            scope = {"type": "http", "headers": [], "method": "GET", "path": "/metrics"}
            request = Request(scope)
            self.assertFalse(authorize_metrics_request(request))

            scope["headers"] = [(b"authorization", b"Bearer secret-token")]
            request = Request(scope)
            self.assertTrue(authorize_metrics_request(request))

    def tearDown(self) -> None:
        get_metrics_config.cache_clear()
        for key in ("METRICS_ENABLED", "METRICS_PUBLIC_ENDPOINT", "METRICS_AUTH_TOKEN", "PROMETHEUS_MULTIPROC_DIR"):
            os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main()
