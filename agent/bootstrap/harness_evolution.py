"""Optional Hub registration; workers never load an orchestration registry."""

from __future__ import annotations


def initialize_harness_evolution(app):
    cfg = (app.config.get("AGENT_CONFIG") or {}).get("evolution") or {}
    if app.config.get("ROLE") != "hub" or cfg.get("harness_engines_enabled") is not True:
        return
    from agent.services.evolution.harness_engines import DspyEvolutionProposalEngine, NativeHarnessEvolutionEngine
    from agent.services.evolution.registry import register_evolution_provider

    for engine in (NativeHarnessEvolutionEngine(), DspyEvolutionProposalEngine()):
        register_evolution_provider(engine, app=app, replace=True)
