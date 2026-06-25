"""Integration tests for security HTTP middleware."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.middleware import security_middleware


def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _security(request, call_next):
        return await security_middleware(request, call_next)

    @app.post("/chat")
    async def chat_endpoint(body: dict):
        return {"answer": body.get("query", ""), "sql_generated": body.get("sql_generated")}

    return app


class SecurityMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_build_test_app())

    def test_blocks_sql_injection_pattern(self) -> None:
        response = self.client.post("/chat", json={"query": "'; DROP TABLE users--"})
        self.assertEqual(response.status_code, 400)

    def test_outbound_redacts_phone_in_answer(self) -> None:
        app = FastAPI()

        @app.middleware("http")
        async def _security(request, call_next):
            return await security_middleware(request, call_next)

        @app.post("/chat")
        async def chat_endpoint(_body: dict):
            return {"answer": "contact 13812345678"}

        client = TestClient(app)
        response = client.post("/chat", json={"query": "hello"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("13812345678", response.json()["answer"])

    def test_inbound_sanitizes_api_key_in_query(self) -> None:
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        response = self.client.post("/chat", json={"query": f"my {secret}"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, response.json()["answer"])

    def test_skips_non_chat_paths(self) -> None:
        app = FastAPI()
        app.middleware("http")(lambda req, call_next: security_middleware(req, call_next))

        @app.get("/health")
        def health():
            return {"status": "ok"}

        client = TestClient(app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)

    def test_skips_outbound_when_agent_already_redacted(self) -> None:
        app = FastAPI()

        @app.middleware("http")
        async def _mark_redacted(request, call_next):
            request.state.security_redacted_at_agent = True
            return await call_next(request)

        @app.middleware("http")
        async def _security(request, call_next):
            return await security_middleware(request, call_next)

        @app.post("/chat")
        async def chat_endpoint(_body: dict):
            return {"answer": "contact 13812345678"}

        client = TestClient(app)
        response = client.post("/chat", json={"query": "hi"})
        self.assertEqual(response.status_code, 200)
        # Middleware skipped outbound scan; raw answer would remain
        self.assertIn("13812345678", response.json()["answer"])


if __name__ == "__main__":
    unittest.main()
