from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from agent.backend_provider_contracts import build_backend_provider_contract_catalog
from agent.local_llm_backends import get_local_openai_backends
from agent.providers.interfaces import ProviderHealthReport
from agent.providers.webcrawler import (
    AnantaWebcrawlerBackendProvider,
    AnantaWebcrawlerProviderConfig,
    AnantaWebcrawlerToolProvider,
    WebcrawlerActionPolicy,
    WebcrawlerConfigError,
    WebcrawlerProviderError,
    WebcrawlerRuntimeManager,
    route_webcrawler_task,
)
from agent.providers.webcrawler.runtime_manager import WebcrawlerRuntimeError
from agent.routes.config.shared import resolve_provider_api_key
from agent.services.ananta_tool_policy_service import (
    DECISION_ALLOW,
    DECISION_APPROVAL_REQUIRED,
    get_ananta_tool_policy_service,
)
from agent.services.ananta_tool_registry_service import get_ananta_tool_registry_service
from agent.services.tool_routing_service import ToolRoutingService
from agent.services.tools import execute_ananta_tool


class _MockWebcrawlerHandler(BaseHTTPRequestHandler):
    server_version = "MockWebcrawler/1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path != "/v1/models":
            self._json(404, {"error": "not found"})
            return
        self._json(
            200,
            {
                "object": "list",
                "data": [
                    {"id": "draft-profile", "object": "model", "status": "draft"},
                    {
                        "id": "news-readonly",
                        "object": "model",
                        "status": "validated",
                        "success_rate": 0.98,
                    },
                ],
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size) or b"{}")
        if self.path.startswith("/v1/adapter/"):
            self._json(200, {"status": "accepted", "profile": payload.get("profile")})
            return
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        model = payload.get("model")
        if model == "missing-profile":
            self._json(404, {"error": "profile missing"})
            return
        if model == "draft-profile":
            self._json(409, {"error": "profile draft"})
            return
        if model == "invalid-profile":
            self._json(422, {"error": "invalid profile"})
            return
        if model == "broken-profile":
            self._json(500, {"error": "execution failed"})
            return
        if payload.get("stream"):
            chunks = [
                {"choices": [{"delta": {"content": "hello "}}]},
                {"choices": [{"delta": {"content": "world"}}]},
            ]
            body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            body += "data: [DONE]\n\n"
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        self._json(
            200,
            {
                "id": "completion-1",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "external result"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                "tool_results": [
                    {
                        "action": "fetch",
                        "status": "ok",
                        "cookie_token": "must-not-leak",
                    }
                ],
            },
        )


@pytest.fixture
def mock_webcrawler_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockWebcrawlerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _config(base_url: str, **overrides: object) -> AnantaWebcrawlerProviderConfig:
    value: dict[str, object] = {
        "enabled": True,
        "mode": "external_url",
        "base_url": base_url,
        "roles": ["backend_provider", "tool_provider"],
        "allowed_profiles": [
            "news-readonly",
            "draft-profile",
            "missing-profile",
            "invalid-profile",
            "broken-profile",
        ],
    }
    value.update(overrides)
    return AnantaWebcrawlerProviderConfig.from_mapping(value)


def test_config_is_disabled_by_default_and_rejects_ambiguous_lifecycle(tmp_path: Path) -> None:
    assert AnantaWebcrawlerProviderConfig.from_mapping({}).mode == "disabled"
    with pytest.raises(WebcrawlerConfigError, match="webcrawler_managed_process_config_incomplete"):
        AnantaWebcrawlerProviderConfig.from_mapping(
            {
                "enabled": True,
                "mode": "managed_process",
                "base_url": "http://127.0.0.1:8787/v1",
                "roles": ["backend_provider"],
                "repo_path": str(tmp_path),
                "startup_command": ["python", "-m", "webcrawler"],
            }
        )
    with pytest.raises(WebcrawlerConfigError, match="webcrawler_startup_command_must_be_argv"):
        AnantaWebcrawlerProviderConfig.from_mapping(
            {
                "enabled": True,
                "mode": "managed_process",
                "base_url": "http://127.0.0.1:8787/v1",
                "roles": ["backend_provider"],
                "repo_path": str(tmp_path),
                "startup_command": "python -m webcrawler",
                "managed_lifecycle_enabled": True,
            }
        )
    with pytest.raises(WebcrawlerConfigError, match="webcrawler_config_unknown_field"):
        AnantaWebcrawlerProviderConfig.from_mapping({"enabledd": True})
    with pytest.raises(WebcrawlerConfigError, match="webcrawler_recording_enabled_invalid"):
        _config("http://127.0.0.1:8787/v1", recording_enabled="yes")
    with pytest.raises(WebcrawlerConfigError, match="webcrawler_healthcheck_path_invalid"):
        _config("http://127.0.0.1:8787/v1", healthcheck_path="/../credentials")
    with pytest.raises(WebcrawlerConfigError, match="webcrawler_openai_contract_invalid"):
        _config("http://127.0.0.1:8787/v1", model_semantics="general_llm_model")


def test_backend_lists_profiles_completes_and_redacts_tool_results(mock_webcrawler_url: str) -> None:
    backend = AnantaWebcrawlerBackendProvider(_config(mock_webcrawler_url))

    profiles = backend.list_profiles()
    assert [item["id"] for item in profiles] == ["draft-profile", "news-readonly"]
    assert profiles[1]["model_semantics"] == "profile_name"

    completion = backend.complete(
        profile="news-readonly",
        messages=[{"role": "user", "content": "latest public news"}],
    )
    assert completion.content == "external result"
    assert completion.tool_results == ({"action": "fetch", "status": "ok", "cookie_token": "***REDACTED***"},)
    assert completion.diagnostics["usage"]["prompt_tokens"] == 3
    assert backend.health().status == "healthy"


def test_backend_streams_sse_without_real_browser(mock_webcrawler_url: str) -> None:
    backend = AnantaWebcrawlerBackendProvider(_config(mock_webcrawler_url))
    chunks = list(
        backend.stream(
            profile="news-readonly",
            messages=[{"role": "user", "content": "stream"}],
        )
    )
    assert [item["choices"][0]["delta"]["content"] for item in chunks] == [
        "hello ",
        "world",
    ]


@pytest.mark.parametrize(
    ("profile", "reason"),
    [
        ("missing-profile", "webcrawler_profile_not_found"),
        ("draft-profile", "webcrawler_profile_draft"),
        ("invalid-profile", "webcrawler_profile_invalid"),
        ("broken-profile", "webcrawler_execution_failed"),
    ],
)
def test_backend_maps_profile_and_execution_errors(
    mock_webcrawler_url: str,
    profile: str,
    reason: str,
) -> None:
    backend = AnantaWebcrawlerBackendProvider(_config(mock_webcrawler_url))
    with pytest.raises(WebcrawlerProviderError, match=reason) as raised:
        backend.complete(profile=profile, messages=[{"role": "user", "content": "run"}])
    assert raised.value.reason_code == reason


def test_tool_provider_exposes_safe_tools_and_structured_audit(mock_webcrawler_url: str) -> None:
    config = _config(mock_webcrawler_url)
    tools = AnantaWebcrawlerToolProvider(
        config,
        AnantaWebcrawlerBackendProvider(config),
        monotonic=iter((10.0, 10.125)).__next__,
    )

    assert {item["name"] for item in tools.catalog()} == {
        "webcrawler.list_profiles",
        "webcrawler.run_profile",
        "webcrawler.get_profile_status",
    }
    result = tools.run(
        tool_name="webcrawler.run_profile",
        arguments={"profile": "news-readonly", "prompt": "collect public data"},
        tool_call_id="call-1",
    )
    assert result["status"] == "ok"
    assert result["data"]["audit"] == {
        "profile": "news-readonly",
        "endpoint": mock_webcrawler_url,
        "mode": "external_url",
        "action": "run_profile",
        "duration_ms": 125,
        "success": True,
    }
    assert "must-not-leak" not in json.dumps(result)
    assert result["evidence"][0]["kind"] == "webcrawler_tool_result"


def test_worker_registry_and_dispatch_use_trusted_hub_config(mock_webcrawler_url: str) -> None:
    registry = get_ananta_tool_registry_service()
    assert registry.get_tool("webcrawler.list_profiles") is not None
    assert registry.get_tool("webcrawler.run_profile") is not None
    assert (
        get_ananta_tool_policy_service()
        .evaluate(tool_name="webcrawler.run_profile")
        .decision
        == DECISION_ALLOW
    )
    assert (
        get_ananta_tool_policy_service()
        .evaluate(tool_name="webcrawler.publish_profile")
        .decision
        == DECISION_APPROVAL_REQUIRED
    )

    provider_config = {
        "enabled": True,
        "mode": "external_url",
        "base_url": mock_webcrawler_url,
        "roles": ["tool_provider"],
        "allowed_profiles": ["news-readonly"],
        "policy_mode": "controlled",
    }
    result = execute_ananta_tool(
        tool_name="webcrawler.run_profile",
        arguments={
            "profile": "news-readonly",
            "prompt": "read public page",
            "action": "login",
            "authorization_granted": True,
        },
        workspace_dir=".",
        tool_call_id="worker-call-1",
        config={"providers": {"ananta_webcrawler": provider_config}},
    )
    assert result["status"] == "policy_blocked"
    assert result["error"] == "webcrawler_session_action_blocked"

    authorized = execute_ananta_tool(
        tool_name="webcrawler.run_profile",
        arguments={
            "profile": "news-readonly",
            "prompt": "refresh authenticated session",
            "action": "login",
        },
        workspace_dir=".",
        tool_call_id="worker-call-2",
        config={
            "providers": {"ananta_webcrawler": provider_config},
            "webcrawler_policy_context": {"authorization_granted": True},
        },
    )
    assert authorized["status"] == "ok"
    assert authorized["policy_decision"]["reason_code"] == "webcrawler_policy_authorized"


def test_write_and_session_actions_need_automatic_hub_authorization(
    mock_webcrawler_url: str,
) -> None:
    config = _config(
        mock_webcrawler_url,
        policy_mode="controlled",
        recording_enabled=True,
        profile_mutation_enabled=True,
    )
    policy = WebcrawlerActionPolicy(config)
    assert policy.decide("login").reason_code == "webcrawler_session_action_blocked"
    assert policy.decide("login", authorization_granted=True).allowed is True
    assert policy.decide("form_submit", authorization_granted=True).risk_class == "critical"

    tools = AnantaWebcrawlerToolProvider(config, AnantaWebcrawlerBackendProvider(config))
    denied = tools.run(
        tool_name="webcrawler.record_flow",
        arguments={"profile": "news-readonly"},
        tool_call_id="call-2",
    )
    assert denied["status"] == "policy_blocked"
    disguised_login = tools.run(
        tool_name="webcrawler.run_profile",
        arguments={
            "profile": "news-readonly",
            "prompt": "log in",
            "action": "login",
        },
        tool_call_id="call-login",
    )
    assert disguised_login["status"] == "policy_blocked"
    assert disguised_login["error"] == "webcrawler_session_action_blocked"
    allowed = tools.run(
        tool_name="webcrawler.record_flow",
        arguments={"profile": "news-readonly", "request": {"start_url": "https://example.com"}},
        tool_call_id="call-3",
        authorization_granted=True,
    )
    assert allowed["status"] == "ok"


def test_routing_is_semantic_only_but_allows_explicit_profile(mock_webcrawler_url: str) -> None:
    config = _config(mock_webcrawler_url)
    assert route_webcrawler_task(config, task_kind="website_ai").selected is True
    code = route_webcrawler_task(config, task_kind="coding")
    assert code.selected is False
    assert code.reason_code == "webcrawler_semantic_mismatch"
    explicit = route_webcrawler_task(
        config,
        task_kind="coding",
        requested_provider="ananta_webcrawler_openai",
        requested_profile="news-readonly",
    )
    assert explicit.selected is True
    assert explicit.explicit is True


def test_hub_tool_router_exposes_semantic_webcrawler_decision(mock_webcrawler_url: str) -> None:
    cfg = {
        "providers": {
            "ananta_webcrawler": {
                "enabled": True,
                "mode": "external_url",
                "base_url": mock_webcrawler_url,
                "roles": ["backend_provider"],
                "allowed_profiles": ["news-readonly"],
            }
        }
    }
    service = ToolRoutingService()
    web = service.route_webcrawler_backend(task_kind="website_ai", agent_cfg=cfg)
    assert web["selected"] is True
    assert web["reason_code"] == "webcrawler_semantic_match"
    code = service.route_webcrawler_backend(task_kind="coding", agent_cfg=cfg)
    assert code["selected"] is False
    explicit = service.route_webcrawler_backend(
        task_kind="coding",
        requested_provider="ananta_webcrawler_openai",
        requested_profile="news-readonly",
        agent_cfg=cfg,
    )
    assert explicit["selected"] is True
    assert explicit["explicit"] is True


def test_generic_worker_and_chat_backend_catalog_exports_webcrawler(
    mock_webcrawler_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANANTA_WEBCRAWLER_API_KEY", "secret-from-environment")
    agent_config = {
        "providers": {
            "ananta_webcrawler": {
                "enabled": True,
                "mode": "external_url",
                "base_url": mock_webcrawler_url,
                "api_key_env": "ANANTA_WEBCRAWLER_API_KEY",
                "roles": ["backend_provider"],
                "allowed_profiles": ["news-readonly"],
            }
        }
    }
    backends = get_local_openai_backends(
        agent_cfg=agent_config,
        default_provider="ananta_webcrawler_openai",
        default_model="news-readonly",
    )
    webcrawler = next(item for item in backends if item["provider"] == "ananta_webcrawler_openai")
    assert webcrawler["transport_provider"] == "openai"
    assert webcrawler["model_semantics"] == "profile_name"
    assert webcrawler["configured_models"] == ["news-readonly"]
    assert webcrawler["api_key"] is None
    assert webcrawler["api_key_env"] == "ANANTA_WEBCRAWLER_API_KEY"
    assert (
        resolve_provider_api_key(
            "ananta_webcrawler_openai",
            None,
            None,
            agent_config,
        )
        == "secret-from-environment"
    )
    contracts = build_backend_provider_contract_catalog()["contracts"]
    contract = next(item for item in contracts if item["provider"] == "ananta_webcrawler_openai")
    assert contract["routing"]["semantic_match_only"] is True


class _HealthSequence:
    def __init__(self, statuses: list[str]) -> None:
        self._statuses = iter(statuses)

    def health(self) -> ProviderHealthReport:
        return ProviderHealthReport(status=next(self._statuses))


class _FakeProcess:
    def __init__(self) -> None:
        self.started: tuple[tuple[str, ...], Path] | None = None
        self.stopped = False

    def start(self, argv: tuple[str, ...], *, cwd: Path) -> None:
        self.started = (argv, cwd)

    def stop(self) -> None:
        self.stopped = True


class _FakeCompose:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, str]] = []

    def up(self, *, compose_file: Path, service: str) -> None:
        self.calls.append(("up", compose_file, service))

    def stop(self, *, compose_file: Path, service: str) -> None:
        self.calls.append(("stop", compose_file, service))

    def restart(self, *, compose_file: Path, service: str) -> None:
        self.calls.append(("restart", compose_file, service))


def test_runtime_manager_requires_policy_and_waits_for_health(tmp_path: Path) -> None:
    config = AnantaWebcrawlerProviderConfig.from_mapping(
        {
            "enabled": True,
            "mode": "managed_process",
            "base_url": "http://127.0.0.1:8787/v1",
            "roles": ["backend_provider"],
            "repo_path": str(tmp_path),
            "startup_command": ["python", "-m", "ananta_webcrawler"],
            "managed_lifecycle_enabled": True,
            "startup_timeout_seconds": 5,
            "health_poll_seconds": 0.1,
        }
    )
    process = _FakeProcess()
    ticks = iter((0.0, 0.1, 0.2, 0.3))
    manager = WebcrawlerRuntimeManager(
        config,
        _HealthSequence(["degraded", "healthy"]),  # type: ignore[arg-type]
        process=process,
        monotonic=ticks.__next__,
        sleeper=lambda _seconds: None,
    )
    with pytest.raises(WebcrawlerRuntimeError, match="webcrawler_lifecycle_policy_blocked"):
        manager.start(lifecycle_authorized=False)
    assert manager.start(lifecycle_authorized=True).status == "healthy"
    assert process.started == (("python", "-m", "ananta_webcrawler"), tmp_path)
    manager.stop(lifecycle_authorized=True)
    assert process.stopped is True


def test_runtime_manager_compose_lifecycle_and_timeout(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    config = AnantaWebcrawlerProviderConfig.from_mapping(
        {
            "enabled": True,
            "mode": "managed_docker_compose",
            "base_url": "http://127.0.0.1:8787/v1",
            "roles": ["backend_provider"],
            "docker_compose_file": str(compose_file),
            "docker_compose_service": "webcrawler",
            "managed_lifecycle_enabled": True,
            "startup_timeout_seconds": 1,
            "health_poll_seconds": 0.1,
        }
    )
    compose = _FakeCompose()
    healthy = WebcrawlerRuntimeManager(
        config,
        _HealthSequence(["healthy", "healthy"]),  # type: ignore[arg-type]
        compose=compose,
        monotonic=iter((0.0, 1.0)).__next__,
        sleeper=lambda _seconds: None,
    )
    assert healthy.start(lifecycle_authorized=True).status == "healthy"
    healthy.restart(lifecycle_authorized=True)
    healthy.stop(lifecycle_authorized=True)
    assert [call[0] for call in compose.calls] == ["up", "restart", "stop"]

    timed_out = WebcrawlerRuntimeManager(
        config,
        _HealthSequence(["degraded", "degraded"]),  # type: ignore[arg-type]
        compose=_FakeCompose(),
        monotonic=iter((0.0, 2.0)).__next__,
        sleeper=lambda _seconds: None,
    )
    with pytest.raises(WebcrawlerRuntimeError, match="webcrawler_startup_timeout"):
        timed_out.start(lifecycle_authorized=True)


@pytest.mark.skipif(
    os.environ.get("ANANTA_WEBCRAWLER_INTEGRATION_TEST") != "1",
    reason="external Webcrawler integration is explicitly opt-in",
)
def test_optional_real_webcrawler_health_contract() -> None:
    base_url = os.environ.get("ANANTA_WEBCRAWLER_BASE_URL", "").strip()
    if not base_url:
        pytest.fail("ANANTA_WEBCRAWLER_BASE_URL is required when integration testing is enabled")
    raw_config = {
        "enabled": True,
        "mode": "external_url",
        "base_url": base_url,
        "roles": ["backend_provider"],
    }
    if os.environ.get("ANANTA_WEBCRAWLER_API_KEY"):
        raw_config["api_key_env"] = "ANANTA_WEBCRAWLER_API_KEY"
    config = AnantaWebcrawlerProviderConfig.from_mapping(raw_config)
    assert AnantaWebcrawlerBackendProvider(config).health().status == "healthy"
