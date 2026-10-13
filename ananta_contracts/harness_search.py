"""Hub-owned immutable search budgets, independent of every candidate genome."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from ananta_contracts.harness_genome import content_digest


@dataclass(frozen=True, slots=True)
class HarnessSearchPolicy:
    max_runs: int = 10
    max_seconds: int = 600
    max_tokens: int = 0
    max_cost: float = 0.0
    tokens_per_run: int = 0
    cost_per_run: float = 0.0
    run_timeout_seconds: int = 30
    max_parallel: int = 1
    max_dispatch_retries: int = 3
    exploration_rate: float = 0.25
    seed: int = 0
    context_budgets: tuple[int, ...] = (32768, 65536, 131072)
    model_context_limit: int = 32768

    def assert_valid(self):
        for value, low, high in (
            (self.max_runs, 1, 100),
            (self.max_seconds, 1, 86400),
            (self.max_tokens, 0, 10**9),
            (self.tokens_per_run, 0, 10**9),
            (self.run_timeout_seconds, 1, 300),
            (self.max_parallel, 1, 64),
            (self.max_dispatch_retries, 1, 10),
            (self.seed, 0, 2**32 - 1),
            (self.model_context_limit, 1024, 8_388_608),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("harness_search_policy_invalid")
        for value in (self.max_cost, self.cost_per_run, self.exploration_rate):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("harness_search_policy_invalid")
        if self.exploration_rate > 1 or not isinstance(self.context_budgets, tuple):
            raise ValueError("harness_search_policy_invalid")
        if any(type(value) is not int or not 1024 <= value <= 131072 for value in self.context_budgets):
            raise ValueError("harness_search_policy_invalid")

    def to_mapping(self):
        self.assert_valid()
        return asdict(self)

    @property
    def digest(self):
        return content_digest(self.to_mapping())

    @classmethod
    def from_mapping(cls, value):
        fields = dict(value)
        fields["context_budgets"] = tuple(fields["context_budgets"])
        policy = cls(**fields)
        policy.assert_valid()
        return policy
