"""Deterministic, telemetry-grounded proposals for the admitted mutation surface."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

from ananta_contracts.harness_genome import HarnessGenome, canonical_json, content_digest


@dataclass(frozen=True, slots=True)
class HarnessTelemetry:
    truncation_events: int = 0
    safe_fusion_opportunities: int = 0

    def __post_init__(self):
        for value in (self.truncation_events, self.safe_fusion_opportunities):
            if type(value) is not int or not 0 <= value <= 1_000_000:
                raise ValueError("harness_telemetry_invalid")


@dataclass(frozen=True, slots=True)
class HarnessHypothesis:
    operator_id: str
    params_json: str
    rationale: str
    priority: int

    @property
    def idea_key(self) -> str:
        # This is a proposal content hash, never a SRC_/RUN_ evidence identity.
        return content_digest({"operator": self.operator_id, "params": json.loads(self.params_json)})

    def to_mapping(self) -> dict:
        return {
            "idea_key": self.idea_key,
            "operator_id": self.operator_id,
            "params": json.loads(self.params_json),
            "rationale": self.rationale,
            "priority": self.priority,
        }


class HarnessHypothesisGenerator:
    def propose(
        self,
        genome: HarnessGenome,
        telemetry: HarnessTelemetry,
        *,
        context_budgets: tuple[int, ...],
        model_context_limit: int,
    ) -> tuple[HarnessHypothesis, ...]:
        genome.assert_valid()
        if type(model_context_limit) is not int or model_context_limit < 1024:
            raise ValueError("harness_model_context_limit_invalid")
        if any(type(value) is not int or not 1024 <= value <= 131072 for value in context_budgets):
            raise ValueError("harness_context_search_space_invalid")
        ideas = []
        if telemetry.truncation_events:
            for budget in sorted(set(context_budgets)):
                if genome.context.budget_tokens < budget <= model_context_limit:
                    ideas.append(
                        HarnessHypothesis(
                            "widen_context",
                            canonical_json({"budget_tokens": budget}),
                            f"{telemetry.truncation_events} truncations: evaluate context budget {budget}",
                            budget,
                        )
                    )
        if telemetry.safe_fusion_opportunities and not genome.tools.action_fusion and genome.tools.allowed_tools:
            ideas.append(
                HarnessHypothesis(
                    "enable_action_fusion",
                    "{}",
                    f"{telemetry.safe_fusion_opportunities} admitted safe fusion opportunities",
                    200000,
                )
            )
        return tuple(ideas)

    @staticmethod
    def choose(
        ideas: tuple[HarnessHypothesis, ...], *, seed: int, attempt: int, exploration_rate: float
    ) -> tuple[HarnessHypothesis, str]:
        if not ideas:
            raise ValueError("harness_hypotheses_empty")
        ordered = sorted(ideas, key=lambda idea: (idea.priority, idea.idea_key))
        rng = random.Random(content_digest({"seed": seed, "attempt": attempt}))
        if rng.random() < exploration_rate:
            return rng.choice(ordered), "explore"
        return ordered[0], "exploit"
