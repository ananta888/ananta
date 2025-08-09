from dataclasses import dataclass, field
from typing import Dict, Any, List


@dataclass
class Agent:
    """Simple agent configuration container."""

    name: str
    tasks: List[str] = field(default_factory=list)
    templates: Dict[str, str] = field(default_factory=dict)
    controller_active: bool = True


def load_agents(config: Dict[str, Any]) -> List[Agent]:
    """Create :class:`Agent` objects from a configuration dictionary.

    The function expects a configuration with an ``"agents"`` list where each
    entry contains at least a ``name`` and optionally ``tasks`` and
    ``templates``. Missing fields are populated with sensible defaults.
    """

    agents: List[Agent] = []
    for entry in config.get("agents", []):
        agents.append(
            Agent(
                name=entry["name"],
                tasks=list(entry.get("tasks", [])),
                templates=dict(entry.get("templates", {})),
                controller_active=entry.get("controller_active", True),
            )
        )
    return agents
