"""Mutation engine for harness genomes and controlled code patches (SEH-009).

Every mutation is typed, justified, bound to a parent and yields a machine-
readable diff. Mutation budgets and allowed target areas are enforced before any
candidate is produced, invalid genomes are rejected before execution, and the
operator selection is deterministic for a given seed so runs are replayable.

Code patches are only admitted for explicitly registered harness modules and are
never admitted for gate, sandbox, security, auth, audit or promotion authority.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

from ananta_contracts.harness_genome import (
    ContextPolicy,
    HarnessGenome,
    content_digest,
)

MUTATION_TYPES = ("parametric", "structural", "prompt", "routing", "tool_policy", "context_policy", "code_patch")
IMMUTABLE_PATCH_MODULES = frozenset({
    "agent/services/hub_evidence_gate_service.py", "agent/services/mutation_gate_service.py",
    "agent/services/harness_runtime.py", "agent/services/memory_policy_gateway.py",
})
MAX_CONTEXT_TOKENS = 131072


class MutationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MutationResult:
    parent_genome_id: str
    child_genome: HarnessGenome
    mutation_type: str
    rationale: str
    diff: dict
    operator_id: str

    def to_mapping(self) -> dict:
        return {"parent_genome_id": self.parent_genome_id, "child_genome_id": self.child_genome.genome_id,
                "mutation_type": self.mutation_type, "operator_id": self.operator_id, "rationale": self.rationale,
                "diff": self.diff}


MutationOperator = Callable[[HarnessGenome, Mapping], HarnessGenome]


def _widen_context(genome: HarnessGenome, params: Mapping) -> HarnessGenome:
    target = int(params.get("budget_tokens", genome.context.budget_tokens * 2))
    if not 1024 <= target <= MAX_CONTEXT_TOKENS:
        raise MutationError("mutation_context_budget_invalid")
    return replace(genome, context=ContextPolicy(
        budget_tokens=target, reserved_completion_tokens=genome.context.reserved_completion_tokens,
        reserved_tool_tokens=genome.context.reserved_tool_tokens))


def _enable_action_fusion(genome: HarnessGenome, params: Mapping) -> HarnessGenome:
    return replace(genome, tools=replace(genome.tools, action_fusion=True))


def _add_few_shot(genome: HarnessGenome, params: Mapping) -> HarnessGenome:
    ref = params.get("ref")
    if not isinstance(ref, str) or not ref:
        raise MutationError("mutation_few_shot_ref_invalid")
    refs = tuple(dict.fromkeys([*genome.prompt.few_shot_refs, ref]))
    return replace(genome, prompt=replace(genome.prompt, few_shot_refs=refs))


DEFAULT_OPERATORS: dict[str, tuple[str, MutationOperator]] = {
    "widen_context": ("context_policy", _widen_context),
    "enable_action_fusion": ("tool_policy", _enable_action_fusion),
    "add_few_shot": ("prompt", _add_few_shot),
}


@dataclass
class MutationEngine:
    operators: Mapping[str, tuple[str, MutationOperator]] = field(default_factory=lambda: DEFAULT_OPERATORS)
    budget: Mapping[str, int] = field(default_factory=lambda: {"context_policy": 3, "tool_policy": 3, "prompt": 3,
                                                               "code_patch": 2})
    allowed_patch_modules: frozenset[str] = frozenset()
    _spent: dict[str, int] = field(default_factory=dict)

    def mutate(self, genome: HarnessGenome, *, operator_id: str, params: Mapping | None = None,
               rationale: str = "") -> MutationResult:
        genome.assert_valid()
        entry = self.operators.get(operator_id)
        if entry is None:
            raise MutationError(f"mutation_operator_unknown:{operator_id}")
        mutation_type, operator = entry
        self._spend(mutation_type)
        try:
            child = operator(genome, params or {})
            child.assert_valid()
        except MutationError:
            raise
        except (ValueError, TypeError) as exc:
            raise MutationError(f"mutation_invalid:{exc}") from None
        if child.genome_id == genome.genome_id:
            raise MutationError("mutation_produced_no_change")
        return MutationResult(parent_genome_id=genome.genome_id, child_genome=child, mutation_type=mutation_type,
                              rationale=rationale, diff=genome.diff(child), operator_id=operator_id)

    def code_patch(self, genome: HarnessGenome, *, module: str, patch_digest: str,
                   rationale: str = "") -> MutationResult:
        genome.assert_valid()
        if module not in self.allowed_patch_modules:
            raise MutationError(f"mutation_patch_module_not_allowed:{module}")
        if module in IMMUTABLE_PATCH_MODULES:
            raise MutationError(f"mutation_patch_module_immutable:{module}")
        if not isinstance(patch_digest, str) or len(patch_digest) != 64:
            raise MutationError("mutation_patch_digest_invalid")
        self._spend("code_patch")
        child = replace(genome, code_hash=patch_digest)
        child.assert_valid()
        return MutationResult(parent_genome_id=genome.genome_id, child_genome=child, mutation_type="code_patch",
                              rationale=rationale, diff=genome.diff(child), operator_id=f"code_patch:{module}")

    def plan(self, genome: HarnessGenome, *, seed: int, count: int) -> list[MutationResult]:
        if type(count) is not int or count < 0:
            raise MutationError("mutation_plan_count_invalid")
        rng = random.Random(seed)
        operator_ids = sorted(self.operators)
        results: list[MutationResult] = []
        for _ in range(count):
            operator_id = rng.choice(operator_ids)
            try:
                results.append(self.mutate(genome, operator_id=operator_id, rationale=f"seed={seed}"))
            except MutationError:
                continue
        return results

    def _spend(self, mutation_type: str) -> None:
        limit = self.budget.get(mutation_type)
        if limit is not None:
            used = self._spent.get(mutation_type, 0)
            if used >= limit:
                raise MutationError(f"mutation_budget_exceeded:{mutation_type}")
            self._spent[mutation_type] = used + 1

    def spent(self) -> dict[str, int]:
        return dict(self._spent)


def mutation_plan_digest(results: list[MutationResult]) -> str:
    return content_digest([result.to_mapping() for result in results])
