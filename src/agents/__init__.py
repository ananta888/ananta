"""Agent factory utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from .base import Agent


def load_agents(config_dir: str | Path = "config/agents") -> Dict[str, Agent]:
    """Load all agent configurations from ``config_dir``.

    Parameters
    ----------
    config_dir:
        Directory containing one JSON file per agent configuration.

    Returns
    -------
    dict[str, Agent]
        Mapping of agent name to :class:`Agent` instances.
    """

    agents: Dict[str, Agent] = {}
    path = Path(config_dir)
    if not path.exists():
        return agents
    for cfg_file in sorted(path.glob("*.json")):
        agent = Agent.from_file(cfg_file)
        agents[agent.name] = agent
    return agents
# Agents-Modul für agentenspezifische Implementierungen

__all__ = ["Agent", "load_agents"]
