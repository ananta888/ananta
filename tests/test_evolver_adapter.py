from __future__ import annotations

from flask import Flask

from agent.services.evolution import EvolutionContext, get_evolution_provider_registry
from agent.services.evolution_service import EvolutionService
from plugins.evolver_adapter import init_app
from plugins.evolver_adapter.adapter import EvolverAdapter


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
