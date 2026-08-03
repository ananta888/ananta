"""Order-independent separation-of-duties decisions for organization slots.

The helper is deliberately persistence-free so every Hub-owned assignment
path applies the same bidirectional role-slot policy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from agent.models.organization_models import SeparationOfDutiesDefinition

SlotSeparationEnforcement = Literal["none", "warn", "strict"]

_ENFORCEMENT_RANK: dict[SlotSeparationEnforcement, int] = {
    "none": 0,
    "warn": 1,
    "strict": 2,
}


@dataclass(frozen=True, slots=True)
class OrganizationSlotSeparationPolicy:
    """The stable identity and SoD declaration of one role slot."""

    slot_id: str
    slot_key: str
    definition: SeparationOfDutiesDefinition


@dataclass(frozen=True, slots=True)
class OrganizationSlotSeparationDecision:
    """The strongest effective policy for one proposed assignment."""

    enforcement: SlotSeparationEnforcement
    conflicting_slot_ids: tuple[str, ...]
    external_duties: tuple[str, ...]

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicting_slot_ids or self.external_duties)


def evaluate_organization_slot_separation(
    *,
    target: OrganizationSlotSeparationPolicy,
    peers: Iterable[OrganizationSlotSeparationPolicy],
    assigned_slot_ids: Iterable[str],
    agent_capabilities: Iterable[str],
) -> OrganizationSlotSeparationDecision:
    """Evaluate a proposed slot assignment against both sides of each pair.

    A relationship may be declared by either role slot.  The strongest
    declaration wins, which makes ``strict`` and ``warn`` behavior invariant
    to assignment order while leaving ``none`` declarations disabled.
    """

    occupied_slot_ids = frozenset(assigned_slot_ids)
    strongest: SlotSeparationEnforcement = "none"
    conflicting_slot_ids: set[str] = set()

    target_independent_keys = frozenset(target.definition.independent_from_slot_ids)
    for peer in peers:
        if peer.slot_id == target.slot_id or peer.slot_id not in occupied_slot_ids:
            continue
        pair_enforcement: SlotSeparationEnforcement = "none"
        if peer.slot_key in target_independent_keys:
            pair_enforcement = _stronger(pair_enforcement, target.definition.enforcement)
        if target.slot_key in frozenset(peer.definition.independent_from_slot_ids):
            pair_enforcement = _stronger(pair_enforcement, peer.definition.enforcement)
        if pair_enforcement == "none":
            continue
        conflicting_slot_ids.add(peer.slot_id)
        strongest = _stronger(strongest, pair_enforcement)

    external_duties = tuple(
        sorted(frozenset(target.definition.independent_from_external_duties) & frozenset(agent_capabilities))
    )
    if external_duties and target.definition.enforcement != "none":
        strongest = _stronger(strongest, target.definition.enforcement)
    elif external_duties:
        external_duties = ()

    return OrganizationSlotSeparationDecision(
        enforcement=strongest,
        conflicting_slot_ids=tuple(sorted(conflicting_slot_ids)),
        external_duties=external_duties,
    )


def _stronger(
    left: SlotSeparationEnforcement,
    right: SlotSeparationEnforcement,
) -> SlotSeparationEnforcement:
    return right if _ENFORCEMENT_RANK[right] > _ENFORCEMENT_RANK[left] else left


__all__ = [
    "OrganizationSlotSeparationDecision",
    "OrganizationSlotSeparationPolicy",
    "SlotSeparationEnforcement",
    "evaluate_organization_slot_separation",
]
