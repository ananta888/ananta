"""DummyModelAdapter — deterministic scripted responses (SIM-016).

Used in tests and dry-runs. Never makes network calls.
"""
from __future__ import annotations

import json
import random
from typing import Any, Callable

from simulation.adapters.base import AdapterResponse, SimulationModelAdapter
from simulation.models.action import ActionProposal, KNOWN_ACTION_TYPES


_DEFAULT_ACTIONS = ["noop", "rest", "move", "eat", "harvest"]


class DummyModelAdapter(SimulationModelAdapter):
    """Returns pre-scripted or randomly chosen noops/actions.

    Args:
        script: Optional list of action dicts replayed in order (then looping).
        action_pool: Pool of action_types to pick from when no script.
        seed: Random seed for reproducibility.
    """

    def __init__(
        self,
        script: list[dict[str, Any]] | None = None,
        action_pool: list[str] | None = None,
        seed: int = 42,
    ) -> None:
        self._script = script or []
        self._pool = action_pool or _DEFAULT_ACTIONS
        self._rng = random.Random(seed)
        self._call_count = 0

    @property
    def provider(self) -> str:
        return "dummy"

    @property
    def model_id(self) -> str:
        return "dummy-v1"

    def generate(self, messages: list[dict[str, str]],
                  agent_id: str, **kwargs: Any) -> AdapterResponse:
        if self._script:
            idx = self._call_count % len(self._script)
            action_dict = dict(self._script[idx])
        else:
            action_type = self._rng.choice(self._pool)
            action_dict = {"action_type": action_type, "reason": "dummy_auto"}

        self._call_count += 1
        action_dict.setdefault("agent_id", agent_id)
        raw = json.dumps(action_dict)

        try:
            proposal = ActionProposal.model_validate(action_dict)
            return AdapterResponse(raw_text=raw, proposal=proposal, model_id=self.model_id)
        except Exception as exc:
            fallback = ActionProposal.invalid_fallback(agent_id, action_dict)
            return AdapterResponse(raw_text=raw, proposal=fallback,
                                    parse_error=str(exc), model_id=self.model_id)


class ScriptedAdapter(DummyModelAdapter):
    """Convenience alias — takes a plain list of action_type strings."""

    def __init__(self, action_types: list[str], seed: int = 0) -> None:
        super().__init__(
            script=[{"action_type": t, "reason": "scripted"} for t in action_types],
            seed=seed,
        )
