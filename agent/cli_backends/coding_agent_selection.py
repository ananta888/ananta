"""Pure, Hub-owned cost and capability policy for coding-agent candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from agent.cli_backends.coding_agent_contract import CodingAgentProbe, FreeClass, ProviderState


class QuotaState(StrEnum):
    AVAILABLE = "available"
    EXHAUSTED = "exhausted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CodingAgentCandidate:
    probe: CodingAgentProbe
    quota_state: QuotaState = QuotaState.UNKNOWN


@dataclass(frozen=True, slots=True)
class CodingAgentSelectionPolicy:
    required_capabilities: frozenset[str] = frozenset()
    allow_paid_or_unknown: bool = False


@dataclass(frozen=True, slots=True)
class CodingAgentSelection:
    provider_id: str | None
    reason_code: str
    considered: tuple[str, ...]


_FREE_PRIORITY = {
    FreeClass.INCLUDED_FREE_INFERENCE: 0,
    FreeClass.FREE_TIER_LIMITED: 1,
    FreeClass.OPEN_SOURCE_BYOK: 2,
    FreeClass.PAID_OR_UNKNOWN: 3,
}


def select_coding_agent(
    candidates: Iterable[CodingAgentCandidate],
    policy: CodingAgentSelectionPolicy,
) -> CodingAgentSelection:
    """Select a ready provider without silently crossing the paid boundary."""

    considered: list[str] = []
    eligible: list[CodingAgentCandidate] = []
    for candidate in candidates:
        descriptor = candidate.probe.descriptor
        considered.append(descriptor.provider_id)
        if candidate.probe.state is not ProviderState.READY:
            continue
        if candidate.quota_state is QuotaState.EXHAUSTED:
            continue
        if descriptor.free_class is FreeClass.PAID_OR_UNKNOWN and not policy.allow_paid_or_unknown:
            continue
        capabilities = descriptor.capabilities.as_dict()
        if any(not capabilities.get(name, False) for name in policy.required_capabilities):
            continue
        eligible.append(candidate)
    if not eligible:
        return CodingAgentSelection(None, "no_policy_eligible_provider", tuple(considered))
    eligible.sort(
        key=lambda item: (
            _FREE_PRIORITY[item.probe.descriptor.free_class],
            item.quota_state is QuotaState.UNKNOWN,
            item.probe.descriptor.provider_id,
        )
    )
    selected = eligible[0].probe.descriptor.provider_id
    return CodingAgentSelection(selected, "free_first_policy_match", tuple(considered))


__all__ = [
    "CodingAgentCandidate",
    "CodingAgentSelection",
    "CodingAgentSelectionPolicy",
    "QuotaState",
    "select_coding_agent",
]
