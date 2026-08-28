"""Hub-owned deterministic refresh and invalidation decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ananta_contracts.parametric_knowledge import ParametricKnowledgeUnit, canonical_sha256


@dataclass(frozen=True, slots=True)
class KnowledgeExpertRefreshPlan:
    unchanged: tuple[str, ...]
    retrain: tuple[str, ...]
    revoke: tuple[str, ...]
    review: tuple[str, ...]
    revoke_manifest_digests: tuple[str, ...]
    plan_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.knowledge-expert-refresh-plan.v1",
            "unchanged": list(self.unchanged),
            "retrain": list(self.retrain),
            "revoke": list(self.revoke),
            "review": list(self.review),
            "revoke_manifest_digests": list(self.revoke_manifest_digests),
            "plan_digest": self.plan_digest,
        }


class KnowledgeExpertRefreshPlanner:
    def plan(
        self,
        *,
        previous: Sequence[ParametricKnowledgeUnit],
        current: Sequence[ParametricKnowledgeUnit],
        dependency_edges: Mapping[str, Sequence[str]],
        unit_manifest_bindings: Mapping[str, Sequence[str]] | None = None,
    ) -> KnowledgeExpertRefreshPlan:
        before = {unit.unit_id: unit for unit in previous}
        after = {unit.unit_id: unit for unit in current}
        removed = set(before).difference(after)
        changed = {
            unit_id
            for unit_id in set(before).intersection(after)
            if before[unit_id].binding_digest != after[unit_id].binding_digest
        }
        revoked = removed | {unit_id for unit_id, unit in after.items() if unit.revoked}
        retrain = changed | set(after).difference(before)
        review: set[str] = set()
        for unit_id, dependencies in dependency_edges.items():
            if set(dependencies).intersection(changed | revoked):
                if unit_id in after and unit_id not in revoked:
                    review.add(unit_id)
        retrain.difference_update(revoked | review)
        unchanged = set(after).difference(retrain | revoked | review)
        payload = {
            "unchanged": sorted(unchanged),
            "retrain": sorted(retrain),
            "revoke": sorted(revoked),
            "review": sorted(review),
            "revoke_manifest_digests": sorted(
                {digest for unit_id in revoked | changed for digest in (unit_manifest_bindings or {}).get(unit_id, ())}
            ),
        }
        return KnowledgeExpertRefreshPlan(
            unchanged=tuple(payload["unchanged"]),
            retrain=tuple(payload["retrain"]),
            revoke=tuple(payload["revoke"]),
            review=tuple(payload["review"]),
            revoke_manifest_digests=tuple(payload["revoke_manifest_digests"]),
            plan_digest=canonical_sha256(payload),
        )


__all__ = ["KnowledgeExpertRefreshPlan", "KnowledgeExpertRefreshPlanner"]
