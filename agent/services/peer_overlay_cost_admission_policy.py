"""Tenant-scoped hard cost quotas for peer-overlay topology admission."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from time import time
from typing import Any, Mapping

from ananta_contracts.peer_overlay import require_overlay_id


@dataclass(frozen=True, slots=True)
class PeerOverlayCostBudget:
    profile_id: str
    version: str
    evidence_revision: str
    evidence_scope: str
    browser: str
    hardware_class: str
    network_profile: str
    measurement_duration_seconds: int
    window_seconds: int
    max_turn_edges: int
    max_peer_relay_edges: int
    max_turn_egress_bytes: int
    max_peer_relay_egress_bytes: int
    reserved_turn_egress_bytes_per_edge: int
    reserved_peer_relay_egress_bytes_per_edge: int

    def __post_init__(self) -> None:
        for field in ("profile_id", "version", "evidence_revision", "browser", "hardware_class", "network_profile"):
            require_overlay_id(getattr(self, field), field)
        if self.evidence_scope not in {"unverified", "test", "local", "external", "production"}:
            raise ValueError("peer_overlay_cost_evidence_scope_invalid")
        values = (
            self.measurement_duration_seconds,
            self.window_seconds,
            self.max_turn_edges,
            self.max_peer_relay_edges,
            self.max_turn_egress_bytes,
            self.max_peer_relay_egress_bytes,
            self.reserved_turn_egress_bytes_per_edge,
            self.reserved_peer_relay_egress_bytes_per_edge,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("peer_overlay_cost_budget_invalid")
        if not 10 <= self.window_seconds <= 3_600:
            raise ValueError("peer_overlay_cost_budget_invalid")


@dataclass(frozen=True, slots=True)
class PeerOverlayCostObservation:
    tenant_id: str
    window_started_at_seconds: int
    turn_egress_bytes: int
    peer_relay_egress_bytes: int

    def __post_init__(self) -> None:
        require_overlay_id(self.tenant_id, "tenant_id")
        values = (self.window_started_at_seconds, self.turn_egress_bytes, self.peer_relay_egress_bytes)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("peer_overlay_cost_observation_invalid")


@dataclass(frozen=True, slots=True)
class PeerOverlayCostDecision:
    allowed: bool
    reason_code: str
    profile_id: str
    profile_version: str
    projected_turn_egress_bytes: int
    projected_peer_relay_egress_bytes: int
    turn_edges: int
    peer_relay_edges: int
    budget_evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class PeerOverlayCostAdmissionPolicy:
    """Apply cost only after mandatory security/consent/quality checks."""

    def __init__(
        self,
        *,
        default_budget: PeerOverlayCostBudget,
        tenant_budgets: Mapping[str, PeerOverlayCostBudget] | None = None,
    ) -> None:
        self._default = default_budget
        self._tenants = {
            require_overlay_id(tenant_id, "tenant_id"): budget
            for tenant_id, budget in dict(tenant_budgets or {}).items()
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> PeerOverlayCostAdmissionPolicy:
        if set(value) != {"schema", "default_profile", "profiles", "tenant_profiles"}:
            raise ValueError("peer_overlay_cost_config_invalid")
        if value.get("schema") != "ananta.peer-overlay-cost-budgets.v1":
            raise ValueError("peer_overlay_cost_config_invalid")
        raw_profiles = value.get("profiles")
        raw_tenants = value.get("tenant_profiles")
        if not isinstance(raw_profiles, Mapping) or not isinstance(raw_tenants, Mapping):
            raise ValueError("peer_overlay_cost_config_invalid")
        profiles = {
            require_overlay_id(profile_id, "profile_id"): PeerOverlayCostBudget(
                profile_id=str(profile_id), **dict(raw)
            )
            for profile_id, raw in raw_profiles.items()
            if isinstance(raw, Mapping)
        }
        if len(profiles) != len(raw_profiles):
            raise ValueError("peer_overlay_cost_config_invalid")
        default_id = require_overlay_id(value.get("default_profile"), "default_profile")
        if default_id not in profiles:
            raise ValueError("peer_overlay_cost_config_invalid")
        tenant_budgets: dict[str, PeerOverlayCostBudget] = {}
        for tenant_id, profile_id in raw_tenants.items():
            tenant = require_overlay_id(tenant_id, "tenant_id")
            selected = require_overlay_id(profile_id, "profile_id")
            if selected not in profiles:
                raise ValueError("peer_overlay_cost_config_invalid")
            tenant_budgets[tenant] = profiles[selected]
        return cls(default_budget=profiles[default_id], tenant_budgets=tenant_budgets)

    def evaluate(
        self,
        *,
        tenant_id: str,
        turn_edges: int,
        peer_relay_edges: int,
        observation: PeerOverlayCostObservation | Mapping[str, Any] | None,
        strict_e2ee_ready: bool,
        relay_consent_complete: bool,
        minimum_quality_met: bool,
        now_seconds: int | None = None,
    ) -> PeerOverlayCostDecision:
        tenant = require_overlay_id(tenant_id, "tenant_id")
        budget = self._tenants.get(tenant, self._default)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (turn_edges, peer_relay_edges)
        ):
            raise ValueError("peer_overlay_cost_edge_count_invalid")
        security_reason = next(
            (
                reason
                for ready, reason in (
                    (strict_e2ee_ready, "peer_overlay_strict_e2ee_required"),
                    (relay_consent_complete, "peer_overlay_relay_consent_required"),
                    (minimum_quality_met, "peer_overlay_minimum_quality_required"),
                )
                if ready is not True
            ),
            None,
        )
        if security_reason:
            return self._decision(False, security_reason, budget, turn_edges, peer_relay_edges, 0, 0)
        if (turn_edges or peer_relay_edges) and observation is None:
            return self._decision(
                False,
                "peer_overlay_cost_observation_missing",
                budget,
                turn_edges,
                peer_relay_edges,
                0,
                0,
            )
        measured = observation if isinstance(observation, PeerOverlayCostObservation) else PeerOverlayCostObservation(
            **dict(
                observation
                or {
                    "tenant_id": tenant,
                    "window_started_at_seconds": 0,
                    "turn_egress_bytes": 0,
                    "peer_relay_egress_bytes": 0,
                }
            )
        )
        if measured.tenant_id != tenant:
            raise ValueError("peer_overlay_cost_tenant_mismatch")
        instant = int(time()) if now_seconds is None else now_seconds
        observation_age = instant - measured.window_started_at_seconds
        if (turn_edges or peer_relay_edges) and not 0 <= observation_age <= budget.window_seconds:
            return self._decision(
                False,
                "peer_overlay_cost_observation_stale",
                budget,
                turn_edges,
                peer_relay_edges,
                measured.turn_egress_bytes,
                measured.peer_relay_egress_bytes,
            )
        projected_turn = measured.turn_egress_bytes + turn_edges * budget.reserved_turn_egress_bytes_per_edge
        projected_peer = (
            measured.peer_relay_egress_bytes
            + peer_relay_edges * budget.reserved_peer_relay_egress_bytes_per_edge
        )
        reason = "peer_overlay_cost_admitted"
        if turn_edges > budget.max_turn_edges or projected_turn > budget.max_turn_egress_bytes:
            reason = "peer_overlay_turn_quota_exceeded"
        elif (
            peer_relay_edges > budget.max_peer_relay_edges
            or projected_peer > budget.max_peer_relay_egress_bytes
        ):
            reason = "peer_overlay_peer_relay_quota_exceeded"
        return self._decision(
            reason == "peer_overlay_cost_admitted",
            reason,
            budget,
            turn_edges,
            peer_relay_edges,
            projected_turn,
            projected_peer,
        )

    @staticmethod
    def _decision(
        allowed: bool,
        reason: str,
        budget: PeerOverlayCostBudget,
        turn_edges: int,
        peer_relay_edges: int,
        projected_turn: int,
        projected_peer: int,
    ) -> PeerOverlayCostDecision:
        return PeerOverlayCostDecision(
            allowed=allowed,
            reason_code=reason,
            profile_id=budget.profile_id,
            profile_version=budget.version,
            projected_turn_egress_bytes=projected_turn,
            projected_peer_relay_egress_bytes=projected_peer,
            turn_edges=turn_edges,
            peer_relay_edges=peer_relay_edges,
            budget_evidence={
                "evidence_revision": budget.evidence_revision,
                "evidence_scope": budget.evidence_scope,
                "browser": budget.browser,
                "hardware_class": budget.hardware_class,
                "network_profile": budget.network_profile,
                "measurement_duration_seconds": budget.measurement_duration_seconds,
                "window_seconds": budget.window_seconds,
            },
        )


__all__ = [
    "PeerOverlayCostAdmissionPolicy",
    "PeerOverlayCostBudget",
    "PeerOverlayCostDecision",
    "PeerOverlayCostObservation",
]
