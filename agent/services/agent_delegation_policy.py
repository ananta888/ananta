"""Hub-only hierarchical delegation policy (PAO-004).

Chain-of-command semantics without worker-to-worker authority: an agent may only
*propose* delegation and the Hub remains the sole task owner. This policy is a
pure validator — it never creates, mutates or claims a task; it returns a
decision and always flags that the Hub must own the resulting task mutation.

Fail-closed rules: the target must be a real subordinate of the proposer in the
acyclic reporting chain, the requested capability must be inside **both** the
target role's and the proposer's capability boundary (no privilege escalation),
and goal/project/task/depth must stay inside the admitted scope.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass

MAX_DELEGATION_DEPTH = 8


@dataclass(frozen=True, slots=True)
class DelegationProposal:
    proposal_id: str
    proposer_role_key: str
    target_role_key: str
    goal_id: str
    project_id: str
    task_id: str | None
    capability: str
    requested_depth: int = 1

    def assert_valid(self) -> None:
        for name, value in (("proposal_id", self.proposal_id), ("proposer_role_key", self.proposer_role_key),
                            ("target_role_key", self.target_role_key), ("goal_id", self.goal_id),
                            ("project_id", self.project_id), ("capability", self.capability)):
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError(f"delegation_{name}_invalid")
        if self.task_id is not None and (not isinstance(self.task_id, str) or not self.task_id):
            raise ValueError("delegation_task_id_invalid")
        if type(self.requested_depth) is not int or not 1 <= self.requested_depth <= MAX_DELEGATION_DEPTH:
            raise ValueError("delegation_depth_invalid")


@dataclass(frozen=True, slots=True)
class DelegationScope:
    goal_id: str
    project_id: str
    max_depth: int = 3

    def assert_valid(self) -> None:
        if not isinstance(self.goal_id, str) or not self.goal_id or not isinstance(self.project_id, str):
            raise ValueError("delegation_scope_invalid")
        if type(self.max_depth) is not int or not 1 <= self.max_depth <= MAX_DELEGATION_DEPTH:
            raise ValueError("delegation_scope_depth_invalid")


@dataclass(frozen=True, slots=True)
class DelegationDecision:
    allowed: bool
    reason: str
    hub_owns_task: bool = True

    @property
    def requires_hub_task_mutation(self) -> bool:
        return True


class HubDelegationPolicy:
    """Validates a delegation proposal against the reporting chain and capabilities."""

    def __init__(self, *, reporting: Mapping[str, str | None]):
        self._reporting = dict(reporting)

    def validate(self, proposal: DelegationProposal, *, role_capabilities: Mapping[str, Set[str]],
                 scope: DelegationScope) -> DelegationDecision:
        try:
            proposal.assert_valid()
            scope.assert_valid()
        except ValueError as exc:
            return DelegationDecision(False, str(exc))
        if scope.goal_id != proposal.goal_id or scope.project_id != proposal.project_id:
            return DelegationDecision(False, "delegation_scope_violation")
        if proposal.requested_depth > scope.max_depth:
            return DelegationDecision(False, "delegation_depth_exceeded")
        if proposal.proposer_role_key == proposal.target_role_key:
            return DelegationDecision(False, "delegation_self_reference")
        chain = self._command_chain(proposal.target_role_key)
        if chain is None:
            return DelegationDecision(False, "delegation_reporting_cycle")
        if proposal.proposer_role_key not in chain:
            return DelegationDecision(False, "delegation_not_in_command_chain")
        proposer_caps = set(role_capabilities.get(proposal.proposer_role_key, set()))
        target_caps = set(role_capabilities.get(proposal.target_role_key, set()))
        if proposal.capability not in target_caps:
            return DelegationDecision(False, "delegation_capability_not_granted")
        if proposal.capability not in proposer_caps:
            return DelegationDecision(False, "delegation_capability_escalation")
        return DelegationDecision(True, "delegation_admitted")

    def _command_chain(self, role_key: str) -> list[str] | None:
        """Managers of ``role_key`` up to the root; ``None`` on a reporting cycle.

        A role that is not present in the reporting map is treated as a root
        (unknown roles simply never contain the proposer), so only a genuine
        reporting cycle yields ``None``.
        """
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = role_key
        while current is not None:
            if current in seen:
                return None
            seen.add(current)
            chain.append(current)
            if current not in self._reporting:
                return chain
            current = self._reporting[current]
        return chain
