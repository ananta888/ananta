from __future__ import annotations

"""Base definitions for agent configuration."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import json


@dataclass(slots=True)
class Agent:
    """Configuration for a single agent.

    Parameters
    ----------
    name:
        Name of the agent. Used as key in registries.
    provider:
        Identifier for the LLM provider (e.g. "openai", "ollama").
    model:
        Name of the model to use.
    prompt_template:
        Template string used to build prompts for the agent.
    config_path:
        Path to the JSON configuration file from which the agent was
        instantiated. Stored for debugging and tracing purposes.
    """

    name: str
    provider: str
    model: str
    prompt_template: str
    config_path: str

    @classmethod
    def from_file(cls, path: str | Path) -> "Agent":
        """Create an :class:`Agent` from a JSON configuration file.

        Parameters
        ----------
        path:
            Path to a JSON file containing the agent definition.

        Returns
        -------
        Agent
            The agent configuration loaded from the file.

        Raises
        ------
        ValueError
            If the JSON file is missing required fields.
        """

        path = Path(path)
        with path.open("r", encoding="utf-8") as fh:
            data: Dict[str, Any] = json.load(fh)

        required = ["name", "provider", "model", "prompt_template"]
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(
                f"Missing required fields {missing!r} in agent config {path!s}"
            )

        return cls(
            name=data["name"],
            provider=data["provider"],
            model=data["model"],
            prompt_template=data["prompt_template"],
            config_path=str(path),
        )
