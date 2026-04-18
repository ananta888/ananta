from __future__ import annotations

import socket
from urllib import error

import pytest
from flask import Flask

from agent.metrics import generate_latest
from agent.services.evolution import EvolutionCapability, EvolutionContext, get_evolution_provider_registry
from agent.services.evolution.engine import UnsupportedEvolutionOperation
from agent.services.evolution_service import EvolutionService
from plugins.evolver_adapter import init_app
from plugins.evolver_adapter.adapter import (
    EvolverAdapter,
    EvolverHttpError,
    EvolverInvalidResponseError,
    EvolverPayloadLimitError,
    EvolverTimeoutError,
    EvolverHttpLimits,
    EvolverRetryPolicy,
    HttpEvolverTransport,
)
from plugins.evolver_adapter.mapper import EvolverResponseSchemaError, map_evolver_result


class FakeEvolverTransport:
    def __init__(self):
        self.payloads: list[dict] = []

    def analyze(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {
            "id": "run-1",
            "status": "completed",
            "summary": "Mapped from Evolver",
            "proposals": [
                {
                    "id": "gene-1",
                    "kind": "gene",
                    "title": "Tune prompt",
                    "description": "Improve the prompt contract.",
                    "risk": "low",
                    "confidence": 0.8,
                    "gene_id": "g-1",
                },
                {
                    "id": "capsule-1",
                    "kind": "capsule",
                    "summary": "Repair failing verification.",
                    "risk_level": "medium",
                },
            ],
        }


def test_evolver_adapter_maps_results_to_generic_evolution_models():
    transport = FakeEvolverTransport()
    adapter = EvolverAdapter(transport=transport, version="test")

    result = adapter.analyze(EvolutionContext(objective="Improve task", task_id="T-EVOLVER"))

    assert result.provider_name == "evolver"
    assert result.summary == "Mapped from Evolver"
    assert [proposal.proposal_type for proposal in result.proposals] == ["improvement", "repair"]
    assert result.proposals[0].provider_metadata["evolver_kind"] == "gene"
    assert result.proposals[0].raw_payload["gene_id"] == "g-1"
    sent_context = transport.payloads[0]["context"]
    assert sent_context["objective"] == "Improve task"
    assert "task_id" not in sent_context
    assert "source_refs" not in sent_context


def test_evolver_plugin_registers_adapter_from_config():
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {
        "evolution": {
            "provider_overrides": {
                "evolver": {
                    "enabled": True,
                    "base_url": "http://evolver:8080",
                    "default": True,
                    "version": "test",
                }
            }
        }
    }
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        init_app(app)
        assert registry.resolve().provider_name == "evolver"
        assert "evolver" in app.extensions["evolution_providers"]
    finally:
        registry.clear()


def test_evolver_plugin_rejects_disallowed_base_url_host():
    app = Flask(__name__)
    app.config["AGENT_CONFIG"] = {
        "evolution": {
            "provider_overrides": {
                "evolver": {
                    "enabled": True,
                    "base_url": "http://unexpected:8080",
                    "allowed_hosts": ["evolver"],
                }
            }
        }
    }
    registry = get_evolution_provider_registry()
    registry.clear()
    try:
        with pytest.raises(ValueError, match="evolver_base_url_host_not_allowed"):
            init_app(app)
    finally:
        registry.clear()


def test_evolver_adapter_reports_real_transport_health():
    class HealthyTransport(FakeEvolverTransport):
        def health(self):
            return {"status": "available"}

    class DownTransport(FakeEvolverTransport):
        def health(self):
            raise EvolverHttpError(503)

    healthy = EvolverAdapter(transport=HealthyTransport()).describe()
    down = EvolverAdapter(transport=DownTransport()).describe()

    assert healthy.status == "available"
    assert healthy.provider_metadata["health_checked"] is True
    assert down.status == "degraded"
    assert down.provider_metadata["last_error"]["code"] == "http_error"


def test_evolver_adapter_exposes_only_truthful_current_capabilities():
    adapter = EvolverAdapter(transport=FakeEvolverTransport())

    assert adapter.supports(EvolutionCapability.ANALYZE) is True
    assert adapter.supports(EvolutionCapability.PROPOSE) is False
    assert adapter.supports(EvolutionCapability.VALIDATE) is False
    assert adapter.supports(EvolutionCapability.APPLY) is False


def test_evolver_adapter_validate_and_apply_fail_closed():
    adapter = EvolverAdapter(transport=FakeEvolverTransport())
    context = EvolutionContext(objective="Improve task", task_id="T-EVOLVER")
    proposal = adapter.analyze(context).proposals[0]

    with pytest.raises(UnsupportedEvolutionOperation, match="validate"):
        adapter.validate(context, proposal)

    with pytest.raises(UnsupportedEvolutionOperation, match="apply"):
        adapter.apply(context, proposal)


def test_evolution_service_audit_distinguishes_evolver_transport_failures():
    class TimeoutTransport(FakeEvolverTransport):
        def analyze(self, payload: dict) -> dict:
            raise EvolverTimeoutError()

    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(EvolverAdapter(transport=TimeoutTransport()), default=True)
    audits: list[tuple[str, dict]] = []
    try:
        service = EvolutionService(registry=registry, audit_fn=lambda action, details: audits.append((action, details)))
        with pytest.raises(EvolverTimeoutError):
            service.analyze(EvolutionContext(objective="Improve task", task_id="T-EVOLVER"))
    finally:
        registry.clear()

    failed = audits[-1]
    assert failed[0] == "evolution_analysis_failed"
    assert failed[1]["error_type"] == "EvolverTimeoutError"
    assert failed[1]["error_code"] == "timeout"
    assert failed[1]["transient"] is True


def test_http_evolver_transport_maps_http_timeout_and_invalid_json(monkeypatch):
    transport = HttpEvolverTransport(base_url="http://evolver:8080", timeout_seconds=1)

    def raise_http_error(*_args, **_kwargs):
        raise error.HTTPError("http://evolver:8080/evolution/analyze", 500, "server error", {}, None)

    monkeypatch.setattr("plugins.evolver_adapter.adapter.request.urlopen", raise_http_error)
    with pytest.raises(EvolverHttpError) as http_exc:
        transport.analyze({"context": {}})
    assert http_exc.value.status_code == 500
    assert http_exc.value.transient is True

    def raise_timeout(*_args, **_kwargs):
        raise socket.timeout()

    monkeypatch.setattr("plugins.evolver_adapter.adapter.request.urlopen", raise_timeout)
    with pytest.raises(EvolverTimeoutError):
        transport.analyze({"context": {}})

    class InvalidJsonResponse:
        status = 200
        sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            if self.sent:
                return b""
            self.sent = True
            return b"{not-json"

    monkeypatch.setattr("plugins.evolver_adapter.adapter.request.urlopen", lambda *_args, **_kwargs: InvalidJsonResponse())
    with pytest.raises(EvolverInvalidResponseError):
        transport.analyze({"context": {}})


def test_http_evolver_transport_injects_headers_and_enforces_payload_limit(monkeypatch):
    captured = {}
    transport = HttpEvolverTransport(
        base_url="http://evolver:8080",
        headers={"Authorization": "Bearer secret-token", "X-Evolver-Tenant": "alpha"},
        limits=EvolverHttpLimits(connect_timeout_seconds=2, read_timeout_seconds=3, max_response_bytes=12),
    )

    class HeaderResponse:
        status = 200
        sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            if self.sent:
                return b""
            self.sent = True
            return b"{}"

    def capture_request(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        captured["timeout"] = timeout
        return HeaderResponse()

    monkeypatch.setattr("plugins.evolver_adapter.adapter.request.urlopen", capture_request)

    assert transport.analyze({"context": {}}) == {}
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["headers"]["X-evolver-tenant"] == "alpha"
    assert captured["timeout"] == 2

    class LargeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return b"x" * 13

    monkeypatch.setattr("plugins.evolver_adapter.adapter.request.urlopen", lambda *_args, **_kwargs: LargeResponse())
    with pytest.raises(EvolverPayloadLimitError):
        transport.analyze({"context": {}})


def test_http_evolver_health_uses_configured_endpoint_or_safe_fallback(monkeypatch):
    fallback_transport = HttpEvolverTransport(base_url="http://evolver:8080")
    assert fallback_transport.health() == {
        "status": "unknown",
        "checked": False,
        "fallback": "health_endpoint_not_configured",
    }

    captured = {}
    transport = HttpEvolverTransport(base_url="http://evolver:8080", health_path="/health")

    class HealthResponse:
        status = 200
        sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            if self.sent:
                return b""
            self.sent = True
            return b'{"status":"available"}'

    def capture_request(req, timeout=None):
        captured["url"] = req.full_url
        return HealthResponse()

    monkeypatch.setattr("plugins.evolver_adapter.adapter.request.urlopen", capture_request)
    assert transport.health()["status"] == "available"
    assert captured["url"] == "http://evolver:8080/health"


def test_http_evolver_transport_retries_transient_failures_and_records_metrics(monkeypatch):
    calls = {"count": 0}
    transport = HttpEvolverTransport(
        base_url="http://evolver:8080",
        retry_policy=EvolverRetryPolicy(max_attempts=2, backoff_seconds=0),
    )

    class SuccessResponse:
        status = 200
        sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            if self.sent:
                return b""
            self.sent = True
            return b'{"status":"completed"}'

    def flaky_urlopen(*_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise error.HTTPError("http://evolver:8080/evolution/analyze", 503, "unavailable", {}, None)
        return SuccessResponse()

    monkeypatch.setattr("plugins.evolver_adapter.adapter.request.urlopen", flaky_urlopen)

    assert transport.analyze({"context": {}}) == {"status": "completed"}
    assert calls["count"] == 2
    metrics_payload = generate_latest().decode("utf-8")
    assert "evolution_provider_retries_total" in metrics_payload
    assert 'error_code="http_error"' in metrics_payload


def test_evolver_response_schema_rejects_invalid_contracts():
    with pytest.raises(EvolverResponseSchemaError, match="proposals"):
        map_evolver_result({"status": "completed", "proposals": {"id": "not-a-list"}})

    with pytest.raises(EvolverResponseSchemaError, match="status"):
        map_evolver_result({"status": 200, "proposals": []})

    with pytest.raises(EvolverResponseSchemaError, match="ambiguous_proposal_sources"):
        map_evolver_result({"status": "completed", "proposals": [], "events": []})


def test_evolver_mapper_handles_explicit_payload_variants_deterministically():
    genes = map_evolver_result(
        {
            "status": "success",
            "proposals": [
                {
                    "id": "gene-1",
                    "kind": "gene",
                    "title": "Tune prompt",
                    "risk": "minimal",
                    "confidence": 0.9,
                }
            ],
        }
    )
    events = map_evolver_result(
        {
            "status": "completed",
            "events": [
                {
                    "event_id": "evt-1",
                    "type": "repair",
                    "summary": "Repair verification",
                    "risk_level": "moderate",
                }
            ],
        }
    )

    assert genes.status == "completed"
    assert genes.proposals[0].proposal_type == "improvement"
    assert genes.proposals[0].risk_level == "low"
    assert genes.proposals[0].provider_metadata["evolver_source_field"] == "proposals"
    assert events.proposals[0].proposal_type == "repair"
    assert events.proposals[0].risk_level == "medium"
    assert events.proposals[0].provider_metadata["evolver_source_field"] == "events"


def test_evolver_env_overrides_populate_provider_config(monkeypatch):
    from agent import config_defaults
    from agent.config_defaults import apply_env_config_overrides, build_default_agent_config

    monkeypatch.setenv("EVOLVER_ENABLED", "1")
    monkeypatch.setenv("EVOLVER_BASE_URL", "http://evolver:8080")
    monkeypatch.setenv("EVOLVER_HEALTH_PATH", "/health")
    monkeypatch.setenv("EVOLVER_CONNECT_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("EVOLVER_READ_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("EVOLVER_MAX_RESPONSE_BYTES", "4096")
    monkeypatch.setenv("EVOLVER_RETRY_COUNT", "2")
    monkeypatch.setenv("EVOLVER_RETRY_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("EVOLVER_BEARER_TOKEN", "secret-token")
    monkeypatch.setenv("EVOLVER_HEADERS", '{"X-Evolver-Tenant":"alpha"}')
    monkeypatch.setenv("EVOLVER_ALLOWED_HOSTS", "evolver,backup-evolver")
    monkeypatch.setenv("EVOLVER_FORCE_ANALYZE_ONLY", "1")
    monkeypatch.setenv("EVOLVER_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("EVOLVER_DEFAULT", "1")
    monkeypatch.setattr(config_defaults.settings, "evolver_enabled", True, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_base_url", "http://evolver:8080", raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_health_path", "/health", raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_connect_timeout_seconds", 2.0, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_read_timeout_seconds", 4.0, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_max_response_bytes", 4096, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_retry_count", 2, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_retry_backoff_seconds", 0.5, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_bearer_token", "secret-token", raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_headers", '{"X-Evolver-Tenant":"alpha"}', raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_allowed_hosts", "evolver,backup-evolver", raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_force_analyze_only", True, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_timeout_seconds", 12.0, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_default", True, raising=False)

    cfg = build_default_agent_config()
    apply_env_config_overrides(cfg)

    evolver_cfg = cfg["evolution"]["provider_overrides"]["evolver"]
    assert evolver_cfg["enabled"] is True
    assert evolver_cfg["base_url"] == "http://evolver:8080"
    assert evolver_cfg["health_path"] == "/health"
    assert evolver_cfg["connect_timeout_seconds"] == 2.0
    assert evolver_cfg["read_timeout_seconds"] == 4.0
    assert evolver_cfg["max_response_bytes"] == 4096
    assert evolver_cfg["retry_count"] == 2
    assert evolver_cfg["retry_backoff_seconds"] == 0.5
    assert evolver_cfg["bearer_token"] == "secret-token"
    assert evolver_cfg["headers"] == {"X-Evolver-Tenant": "alpha"}
    assert evolver_cfg["allowed_hosts"] == ["evolver", "backup-evolver"]
    assert evolver_cfg["force_analyze_only"] is True
    assert evolver_cfg["timeout_seconds"] == 12.0
    assert cfg["evolution"]["default_provider"] == "evolver"


def test_evolution_service_uses_evolver_adapter_without_core_special_case():
    transport = FakeEvolverTransport()
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(EvolverAdapter(transport=transport), default=True)
    try:
        service = EvolutionService(registry=registry, audit_fn=lambda *_: None)
        result = service.analyze(EvolutionContext(objective="Improve task", task_id="T-EVOLVER"))
        assert result.provider_name == "evolver"
        assert result.proposals[0].title == "Tune prompt"
    finally:
        registry.clear()
