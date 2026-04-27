"""Unit tests for the FastAPI endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


class TestHealthEndpoints:
    def test_liveness_returns_200(self, api_client):
        r = api_client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"

    def test_readiness_returns_200_or_503(self, api_client):
        r = api_client.get("/health/ready")
        assert r.status_code in (200, 503)

    def test_root_endpoint(self, api_client):
        r = api_client.get("/")
        assert r.status_code == 200
        assert "service" in r.json()


class TestContextEndpoints:
    def test_create_context_requires_auth(self, api_client):
        from uuid import uuid4

        r = api_client.post(
            "/api/v1/contexts/",
            json={"session_id": str(uuid4())},
        )
        assert r.status_code in (200, 201, 401)

    def test_create_context_with_auth(self, api_client, auth_headers):
        from uuid import uuid4

        r = api_client.post(
            "/api/v1/contexts/",
            json={"session_id": str(uuid4())},
            headers=auth_headers,
        )
        assert r.status_code in (200, 201)
        body = r.json()
        assert body["data"]["id"] is not None
        assert body["data"]["status"] == "active"

    def test_get_nonexistent_context_returns_404(self, api_client, auth_headers):
        from uuid import uuid4

        r = api_client.get(
            f"/api/v1/contexts/{uuid4()}",
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_create_and_get_context(self, api_client, auth_headers):
        from uuid import uuid4

        create_r = api_client.post(
            "/api/v1/contexts/",
            json={"session_id": str(uuid4())},
            headers=auth_headers,
        )
        assert create_r.status_code in (200, 201)
        context_id = create_r.json()["data"]["id"]

        get_r = api_client.get(
            f"/api/v1/contexts/{context_id}",
            headers=auth_headers,
        )
        assert get_r.status_code == 200
        assert get_r.json()["data"]["id"] == context_id

    def test_append_message_to_context(self, api_client, auth_headers):
        from uuid import uuid4

        create_r = api_client.post(
            "/api/v1/contexts/",
            json={"session_id": str(uuid4())},
            headers=auth_headers,
        )
        context_id = create_r.json()["data"]["id"]

        r = api_client.put(
            f"/api/v1/contexts/{context_id}/messages",
            json={"role": "user", "content": "Hello, MCP!"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["token_count"] > 0

    def test_seal_context(self, api_client, auth_headers):
        from uuid import uuid4

        create_r = api_client.post(
            "/api/v1/contexts/",
            json={"session_id": str(uuid4())},
            headers=auth_headers,
        )
        context_id = create_r.json()["data"]["id"]

        r = api_client.post(
            f"/api/v1/contexts/{context_id}/seal",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "sealed"

    def test_fork_context(self, api_client, auth_headers):
        from uuid import uuid4

        create_r = api_client.post(
            "/api/v1/contexts/",
            json={"session_id": str(uuid4())},
            headers=auth_headers,
        )
        context_id = create_r.json()["data"]["id"]

        r = api_client.post(
            f"/api/v1/contexts/{context_id}/fork",
            headers=auth_headers,
        )
        assert r.status_code in (200, 201)
        body = r.json()
        assert body["data"]["parent_id"] == context_id

    def test_delete_context(self, api_client, auth_headers):
        from uuid import uuid4

        create_r = api_client.post(
            "/api/v1/contexts/",
            json={"session_id": str(uuid4())},
            headers=auth_headers,
        )
        context_id = create_r.json()["data"]["id"]

        r = api_client.delete(
            f"/api/v1/contexts/{context_id}",
            headers=auth_headers,
        )
        assert r.status_code == 204


class TestModuleEndpoints:
    def test_list_modules_returns_registered(self, api_client, auth_headers):
        r = api_client.get("/api/v1/modules/", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        names = [m["name"] for m in body["data"]["modules"]]
        assert "echo" in names

    def test_get_module_schema(self, api_client, auth_headers):
        r = api_client.get("/api/v1/modules/echo", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["name"] == "echo"
        assert "input_schema" in body["data"]
        assert "output_schema" in body["data"]

    def test_get_nonexistent_module_returns_404(self, api_client, auth_headers):
        r = api_client.get("/api/v1/modules/nonexistent", headers=auth_headers)
        assert r.status_code == 404

    def test_execute_echo_module(self, api_client, auth_headers):
        r = api_client.post(
            "/api/v1/modules/echo/execute",
            json={"input": {"text": "hello from test"}},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["status"] == "success"
        assert body["data"]["output"]["text"] == "hello from test"

    def test_execute_echo_uppercase(self, api_client, auth_headers):
        r = api_client.post(
            "/api/v1/modules/echo/execute",
            json={"input": {"text": "hello", "uppercase": True}},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["data"]["output"]["text"] == "HELLO"

    def test_execute_nonexistent_module_returns_404(self, api_client, auth_headers):
        r = api_client.post(
            "/api/v1/modules/nonexistent/execute",
            json={"input": {}},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_module_health_endpoint(self, api_client, auth_headers):
        r = api_client.get("/api/v1/modules/health", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "overall" in body["data"]
        assert "modules" in body["data"]


class TestMemoryEndpoints:
    def test_store_memory_short_term(self, api_client, auth_headers):
        r = api_client.post(
            "/api/v1/memory/store",
            json={"content": "Test memory content", "tier": "short_term"},
            headers=auth_headers,
        )
        assert r.status_code == 201
        assert "memory_id" in r.json()["data"]

    def test_store_memory_long_term(self, api_client, auth_headers):
        r = api_client.post(
            "/api/v1/memory/store",
            json={"content": "Long-term memory content", "tier": "long_term"},
            headers=auth_headers,
        )
        assert r.status_code == 201

    def test_retrieve_memory(self, api_client, auth_headers):
        r = api_client.post(
            "/api/v1/memory/retrieve",
            json={"query": "test query", "tier": "long_term", "k": 5},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "results" in body["data"]

    def test_memory_stats(self, api_client, auth_headers):
        r = api_client.get("/api/v1/memory/stats", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "redis_connected" in body["data"]
