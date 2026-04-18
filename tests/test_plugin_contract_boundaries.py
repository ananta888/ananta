import pytest
from flask import Flask
from agent.services.evolution.registry import get_evolution_provider_registry
from agent.services.evolution.engine import EvolutionEngine
from agent.services.evolution.models import EvolutionContext, EvolutionResult, ValidationResult, ApplyResult, EvolutionProposal

def test_evolution_provider_contract_compliance():
    """
    Stellt sicher, dass alle registrierten Evolution-Provider dem EvolutionEngine-Kontrakt entsprechen.
    Dies schuetzt vor Architekturdrift in Plugin-Schnittstellen (BND-012).
    """
    registry = get_evolution_provider_registry()
    descriptors = registry.list_descriptors()

    if not descriptors:
        pytest.skip("Keine Evolution-Provider registriert.")

    for desc in descriptors:
        name = desc["provider_name"]
        provider = registry.get(name)
        assert isinstance(provider, EvolutionEngine), f"Provider {provider.provider_name} muss von EvolutionEngine erben"

        # Pruefe Basiseigenschaften
        assert hasattr(provider, 'provider_name'), f"Provider {provider.__class__} fehlt provider_name"
        assert isinstance(provider.provider_name, str)
        assert hasattr(provider, 'capabilities'), f"Provider {provider.provider_name} fehlt capabilities"

        # Pruefe Analyse-Kontrakt (Pflicht)
        assert hasattr(provider, 'analyze'), f"Provider {provider.provider_name} muss analyze implementieren"

        # Pruefe, ob die Signatur grob passt (statische Typen koennen wir hier schwer zur Laufzeit pruefen,
        # aber wir koennen einen Dummy-Aufruf mit Validierungs-Error-Erwartung machen oder
        # einfach die Existenz der Methoden pruefen)
        assert callable(provider.analyze)
        assert callable(provider.validate)
        assert callable(provider.apply)

def test_evolution_models_structure_stability():
    """
    Prueft die Stabilitaet der Kern-Modelle, die als Datenaustausch-Schnittstelle dienen.
    Aenderungen hier muessen bewusst vorgenommen werden.
    """
    # Stichprobe wichtiger Felder in EvolutionResult (Contract)
    res = EvolutionResult(provider_name="test")
    assert hasattr(res, "run_id")
    assert hasattr(res, "status")
    assert hasattr(res, "proposals")
    assert isinstance(res.proposals, list)

    # Stichprobe EvolutionProposal
    prop = EvolutionProposal(title="Test", description="Test")
    assert hasattr(prop, "proposal_id")
    assert hasattr(prop, "target_refs")

def test_plugin_interface_documentation_alignment():
    """
    Prueft, ob die in docs/extensions.md beschriebenen Seams im Code auffindbar sind.
    """
    from agent.sdk import AnantaSDK
    sdk_methods = [m for m in dir(AnantaSDK) if not m.startswith('_')]

    # Laut Doku muessen diese existieren:
    expected = ['register_evolution_provider', 'register_blueprint', 'get_config']
    for exp in expected:
        assert exp in sdk_methods, f"SDK-Methode {exp} fehlt, aber ist in docs/extensions.md dokumentiert"
