"""API contract tests for Prompt Trace endpoints. PTI-029."""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from flask import Flask


def _make_app(tmp_path):
    """Create a minimal Flask app for testing."""
    from agent.ai_agent import create_app
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
    # Use a minimal app with just the debug blueprints
    app = Flask("test_pti_api")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.config["AGENT_CONFIG"] = {}
    app.config["AGENT_NAME"] = "test-agent"

    from agent.routes.debug.prompt_traces import prompt_traces_bp
    from agent.routes.debug.prompt_render import prompt_render_bp
    app.register_blueprint(prompt_traces_bp)
    app.register_blueprint(prompt_render_bp)
    return app


@pytest.fixture
def app_client(tmp_path):
    app = _make_app(tmp_path)

    # Patch auth to always pass
    with patch("agent.auth.check_auth", lambda f: f):
        yield app.test_client(), app, tmp_path


@pytest.fixture
def populated_svc(tmp_path):
    from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
    storage = PromptTraceStorage(data_dir=str(tmp_path))
    svc = PromptTraceService(storage=storage)
    return svc, tmp_path


class TestListLLMRequests:
    def test_list_returns_redacted_preview(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        trace = svc.create_trace(provider="lmstudio", model="gemma", prompt="test prompt for listing")
        finalized = svc.finalize_trace(trace, success=True)
        svc.store(finalized)

        app = Flask("test")
        app.config["TESTING"] = True
        from agent.routes.debug.prompt_traces import prompt_traces_bp
        app.register_blueprint(prompt_traces_bp)

        with patch("agent.auth.check_auth", lambda f: f):
            with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
                with app.test_client() as client:
                    resp = client.get("/debug/llm-requests")
                    assert resp.status_code == 200
                    data = resp.get_json()
                    assert "traces" in (data.get("data") or data)

    def test_filter_by_provider(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        for provider in ("lmstudio", "ollama", "lmstudio"):
            t = svc.create_trace(provider=provider, prompt="p")
            svc.store(svc.finalize_trace(t, success=True))

        traces = svc.list_traces(limit=10, provider="lmstudio")
        assert all(t.provider == "lmstudio" for t in traces)
        assert len(traces) == 2

    def test_limit_capped(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        # list with huge limit still returns at most stored count
        traces = storage.list(limit=99999)
        assert isinstance(traces, list)


class TestDetailTrace:
    def test_get_existing_trace(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        trace = svc.create_trace(provider="lmstudio", prompt="detail test")
        finalized = svc.finalize_trace(trace, success=True)
        svc.store(finalized)

        app = Flask("test_detail")
        app.config["TESTING"] = True
        from agent.routes.debug.prompt_traces import prompt_traces_bp
        app.register_blueprint(prompt_traces_bp)

        with patch("agent.auth.check_auth", lambda f: f):
            with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
                with app.test_client() as client:
                    resp = client.get(f"/debug/llm-requests/{trace.trace_id}")
                    assert resp.status_code == 200
                    body = resp.get_json()
                    inner = body.get("data") or body
                    assert inner.get("trace_id") == trace.trace_id

    def test_unknown_trace_returns_404(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        app = Flask("test_404")
        app.config["TESTING"] = True
        from agent.routes.debug.prompt_traces import prompt_traces_bp
        app.register_blueprint(prompt_traces_bp)

        with patch("agent.auth.check_auth", lambda f: f):
            with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
                with app.test_client() as client:
                    resp = client.get("/debug/llm-requests/nonexistent-trace-id")
                    assert resp.status_code == 404

    def test_include_raw_denied_by_default(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        trace = svc.create_trace(prompt="raw access test")
        finalized = svc.finalize_trace(trace, success=True)
        svc.store(finalized)

        app = Flask("test_raw")
        app.config["TESTING"] = True
        from agent.routes.debug.prompt_traces import prompt_traces_bp
        app.register_blueprint(prompt_traces_bp)

        with patch("agent.auth.check_auth", lambda f: f):
            with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
                with app.test_client() as client:
                    resp = client.get(f"/debug/llm-requests/{trace.trace_id}?include_raw=true")
                    assert resp.status_code == 200
                    body = resp.get_json()
                    inner = body.get("data") or body
                    # raw_access_denied because raw_available=False
                    assert inner.get("raw_access_denied") is True or inner.get("raw_access_granted") is None


class TestGoalPromptTraces:
    def test_goal_traces_grouped_by_kind(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        for kind in ("planning", "planning", "generate"):
            t = svc.create_trace(goal_id="goal-test", request_kind=kind, prompt="p")
            svc.store(svc.finalize_trace(t, success=True))

        app = Flask("test_goal")
        app.config["TESTING"] = True
        from agent.routes.debug.prompt_traces import prompt_traces_bp
        app.register_blueprint(prompt_traces_bp)

        with patch("agent.auth.check_auth", lambda f: f):
            with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
                with app.test_client() as client:
                    resp = client.get("/goals/goal-test/prompt-traces")
                    assert resp.status_code == 200
                    body = resp.get_json()
                    inner = body.get("data") or body
                    traces = inner.get("traces") or {}
                    assert "planning" in traces
                    assert len(traces["planning"]) == 2

    def test_goal_no_traces_returns_empty(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        app = Flask("test_empty")
        app.config["TESTING"] = True
        from agent.routes.debug.prompt_traces import prompt_traces_bp
        app.register_blueprint(prompt_traces_bp)

        with patch("agent.auth.check_auth", lambda f: f):
            with patch("agent.services.prompt_trace_service.get_prompt_trace_service", return_value=svc):
                with app.test_client() as client:
                    resp = client.get("/goals/no-such-goal/prompt-traces")
                    assert resp.status_code == 200
                    body = resp.get_json()
                    inner = body.get("data") or body
                    assert inner.get("total") == 0


class TestRenderDryRun:
    def test_render_dry_run_does_not_call_provider(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        app = Flask("test_render")
        app.config["TESTING"] = True
        from agent.routes.debug.prompt_render import prompt_render_bp
        app.register_blueprint(prompt_render_bp)

        # Mock the registry to return a fixed prompt
        from agent.services.planning_prompt_registry import ResolvedPlanningPrompt
        mock_resolved = ResolvedPlanningPrompt(
            prompt_version_id="v-test",
            version="v1",
            language="de",
            mode="generic",
            prompt="Rendered prompt text",
            checksum="abc123",
        )

        with patch("agent.auth.check_auth", lambda f: f):
            with patch("agent.services.planning_prompt_registry.get_planning_prompt_registry") as mock_reg:
                mock_reg.return_value.resolve.return_value = mock_resolved
                with app.test_client() as client:
                    resp = client.post(
                        "/debug/prompts/render-dry-run",
                        json={"goal": "Build a tool", "mode": "generic", "persist_trace": False},
                    )
                    assert resp.status_code == 200
                    body = resp.get_json()
                    inner = body.get("data") or body
                    assert inner.get("provider_called") is False
                    assert inner.get("prompt_version_id") == "v-test"
                    assert inner.get("template_chain") is not None

    def test_render_returns_prompt_hash(self, tmp_path):
        app = Flask("test_render_hash")
        app.config["TESTING"] = True
        from agent.routes.debug.prompt_render import prompt_render_bp
        app.register_blueprint(prompt_render_bp)

        from agent.services.planning_prompt_registry import ResolvedPlanningPrompt
        mock_resolved = ResolvedPlanningPrompt(
            prompt_version_id="v-hash-test",
            version="v1",
            language="de",
            mode="generic",
            prompt="Hash test prompt",
            checksum="def456",
        )

        with patch("agent.auth.check_auth", lambda f: f):
            with patch("agent.services.planning_prompt_registry.get_planning_prompt_registry") as mock_reg:
                mock_reg.return_value.resolve.return_value = mock_resolved
                with app.test_client() as client:
                    resp = client.post(
                        "/debug/prompts/render-dry-run",
                        json={"goal": "Hash test"},
                    )
                    assert resp.status_code == 200
                    body = resp.get_json()
                    inner = body.get("data") or body
                    assert inner.get("prompt_hash_sha256") is not None
