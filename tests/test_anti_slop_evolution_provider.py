import pytest

from agent.services.evolution.engine import UnsupportedEvolutionOperation
from agent.services.evolution.models import EvolutionCapability, EvolutionContext
from plugins.anti_slop_evaluator.provider import AntiSlopEvolutionProvider


def test_provider_is_analyze_only(monkeypatch):
    provider = AntiSlopEvolutionProvider()
    assert provider.supports(EvolutionCapability.ANALYZE)
    assert not provider.supports(EvolutionCapability.APPLY)
    with pytest.raises(UnsupportedEvolutionOperation):
        provider.apply(EvolutionContext(objective="test"), None)
    result = provider.analyze(EvolutionContext(objective="not an authorized text source"))
    assert result.status == "degraded"
    assert not result.proposals
