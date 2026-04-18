from __future__ import annotations

import socket
from urllib import error

import pytest
from flask import Flask

from agent.services.evolution import EvolutionCapability, EvolutionContext, get_evolution_provider_registry
from agent.services.evolution_service import EvolutionService
from plugins.evolver_adapter import init_app
from plugins.evolver_adapter.adapter import (
    EvolverAdapter,
    EvolverHttpError,
    EvolverInvalidResponseError,
    EvolverTimeoutError,
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
    assert transport.payloads[0]["context"]["task_id"] == "T-EVOLVER"


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

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{not-json"

    monkeypatch.setattr("plugins.evolver_adapter.adapter.request.urlopen", lambda *_args, **_kwargs: InvalidJsonResponse())
    with pytest.raises(EvolverInvalidResponseError):
        transport.analyze({"context": {}})


def test_evolver_response_schema_rejects_invalid_contracts():
    with pytest.raises(EvolverResponseSchemaError, match="proposals"):
        map_evolver_result({"status": "completed", "proposals": {"id": "not-a-list"}})

    with pytest.raises(EvolverResponseSchemaError, match="status"):
        map_evolver_result({"status": 200, "proposals": []})


def test_evolver_env_overrides_populate_provider_config(monkeypatch):
    from agent import config_defaults
    from agent.config_defaults import apply_env_config_overrides, build_default_agent_config

    monkeypatch.setenv("EVOLVER_ENABLED", "1")
    monkeypatch.setenv("EVOLVER_BASE_URL", "http://evolver:8080")
    monkeypatch.setenv("EVOLVER_HEALTH_PATH", "/health")
    monkeypatch.setenv("EVOLVER_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("EVOLVER_DEFAULT", "1")
    monkeypatch.setattr(config_defaults.settings, "evolver_enabled", True, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_base_url", "http://evolver:8080", raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_health_path", "/health", raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_timeout_seconds", 12.0, raising=False)
    monkeypatch.setattr(config_defaults.settings, "evolver_default", True, raising=False)

    cfg = build_default_agent_config()
    apply_env_config_overrides(cfg)

    evolver_cfg = cfg["evolution"]["provider_overrides"]["evolver"]
    assert evolver_cfg["enabled"] is True
    assert evolver_cfg["base_url"] == "http://evolver:8080"
    assert evolver_cfg["health_path"] == "/health"
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
