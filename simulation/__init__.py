"""Ananta Emergence Simulation & Governance Lab.

Entry-points
------------
  simulation.engine.tick_runner   — TickRunner (run a scenario)
  simulation.engine.replay        — DeterministicReplayEngine
  simulation.adapters.dummy       — DummyModelAdapter (CI, no LLM)
  simulation.cli.commands         — CLI/TUI integration
  simulation.scenarios            — built-in scenario presets

No hard dependency on LangGraph, n8n, Ollama or remote APIs at import time.
"""
__version__ = "0.1.0"
