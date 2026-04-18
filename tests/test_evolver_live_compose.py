from __future__ import annotations

from agent.ai_agent import create_app
from agent.services.evolution import EvolutionContext, get_evolution_provider_registry
from agent.services.evolution_service import EvolutionService


def test_evolver_live_compose_provider_registered_and_analyze_works():
    app = create_app(agent="evolver-live-test")

    with app.app_context():
        registry = get_evolution_provider_registry()
        provider = registry.resolve("evolver")
        assert provider.provider_name == "evolver"

        descriptor = provider.describe()
        assert descriptor.status in {"available", "ok"}
        assert descriptor.provider_metadata.get("health_checked") is True

        service = EvolutionService(registry=registry, audit_fn=lambda *_: None)
        result = service.analyze(
            EvolutionContext(
                objective="Find a controlled improvement proposal for a failing task",
                task_id="T-EVOLVER-LIVE-001",
                trace_id="TRACE-EVOLVER-LIVE-001",
            )
        )

    assert result.provider_name == "evolver"
    assert result.status in {"completed", "ok"}
    assert isinstance(result.proposals, list)
    assert len(result.proposals) >= 1
    assert result.proposals[0].requires_review is True
