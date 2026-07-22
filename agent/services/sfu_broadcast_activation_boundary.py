"""Hub-owned, fail-closed activation boundary for SFU broadcast fanout."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Mapping

from agent.services.sfu_broadcast_source_grounding import GroundingDecision


_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
_ACTIVE_PARENT_STAGES = frozenset({"canary", "active"})
_MODE_PLACEMENT_OWNER = {
    "livekit_control_api": "livekit_native",
    "authenticated_runtime_extension": "livekit_native",
}
_MODE_CAPABILITIES = {
    "livekit_control_api": frozenset(
        {
            "room_lifecycle",
            "participant_permissions",
            "subscription_permissions",
            "publisher_role_authorization",
            "active_speaker_callbacks",
            "layer_feedback",
            "runtime_metrics",
            "health_readiness",
        }
    ),
    "authenticated_runtime_extension": frozenset(
        {
            "authenticated_control",
            "room_lifecycle",
            "participant_permissions",
            "runtime_metrics",
            "health_readiness",
        }
    ),
}
_FEATURE_DEPENDENCIES = {
    "semantic_media_receiver_groups": frozenset({"semantic_media_broadcast"}),
    "semantic_media_fleet_admission": frozenset({"semantic_media_broadcast"}),
    "semantic_media_turn_cost_controls": frozenset({"semantic_media_broadcast"}),
}


@dataclass(frozen=True)
class ParentReadinessSnapshot:
    decision: str
    rollout_stage: str
    manifest_version: int
    fresh_until: datetime | None
    source_commit: str | None
    config_digest: str | None


@dataclass(frozen=True)
class RuntimeCapabilitySnapshot:
    selected_mode: str
    effective_mode: str
    placement_owner: str
    evidence_status: str
    image_digest: str | None
    config_digest: str | None
    capabilities: Mapping[str, bool]


@dataclass(frozen=True)
class FeaturePolicySnapshot:
    policy_version: int
    repository_available: bool
    kill_switch_active: bool
    flags: Mapping[str, bool]


@dataclass(frozen=True)
class LimitsSnapshot:
    status: str
    policy_version: int
    participant_cap: int


@dataclass(frozen=True)
class ActivationRequest:
    expected_parent_manifest_version: int
    expected_feature_policy_version: int
    expected_limit_policy_version: int
    requested_features: Mapping[str, bool]


@dataclass(frozen=True)
class ActivationDecision:
    status: str
    reason_codes: tuple[str, ...]
    effective_mode: str
    effective_features: Mapping[str, bool]
    participant_cap: int
    rollback_required: bool


class SfuBroadcastActivationBoundary:
    """Combines projections without assuming infrastructure ownership."""

    def evaluate(
        self,
        *,
        now: datetime,
        request: ActivationRequest,
        parent: ParentReadinessSnapshot,
        runtime: RuntimeCapabilitySnapshot,
        feature_policy: FeaturePolicySnapshot,
        limits: LimitsSnapshot,
        grounding: GroundingDecision,
    ) -> ActivationDecision:
        if now.tzinfo is None:
            raise ValueError("now_must_be_timezone_aware")
        now = now.astimezone(UTC)
        reasons: set[str] = set()

        if parent.decision != "go":
            reasons.add("parent_decision_not_go")
        if parent.rollout_stage not in _ACTIVE_PARENT_STAGES:
            reasons.add("parent_rollout_stage_not_active")
        if (
            parent.manifest_version <= 0
            or parent.manifest_version != request.expected_parent_manifest_version
        ):
            reasons.add("parent_manifest_version_mismatch")
        if parent.fresh_until is None:
            reasons.add("parent_readiness_freshness_missing")
        elif (
            parent.fresh_until.tzinfo is None
            or parent.fresh_until.astimezone(UTC) <= now
        ):
            reasons.add("parent_readiness_stale")
        if parent.source_commit is None or not _COMMIT.fullmatch(parent.source_commit):
            reasons.add("parent_source_commit_invalid")
        if parent.config_digest is None or not _DIGEST.fullmatch(parent.config_digest):
            reasons.add("parent_config_digest_invalid")

        if runtime.selected_mode not in _MODE_PLACEMENT_OWNER:
            reasons.add("runtime_mode_not_closed")
        if runtime.effective_mode != runtime.selected_mode:
            reasons.add("runtime_mode_not_effective")
        expected_owner = _MODE_PLACEMENT_OWNER.get(runtime.selected_mode)
        if expected_owner is None or runtime.placement_owner != expected_owner:
            reasons.add("runtime_placement_owner_mismatch")
        if runtime.evidence_status != "verified":
            reasons.add("runtime_evidence_not_verified")
        if runtime.image_digest is None or not _DIGEST.fullmatch(runtime.image_digest):
            reasons.add("runtime_image_digest_invalid")
        if runtime.config_digest is None or not _DIGEST.fullmatch(runtime.config_digest):
            reasons.add("runtime_config_digest_invalid")
        required = _MODE_CAPABILITIES.get(runtime.selected_mode, frozenset())
        if any(runtime.capabilities.get(capability) is not True for capability in required):
            reasons.add("runtime_capability_missing")

        if not feature_policy.repository_available:
            reasons.add("feature_policy_repository_unavailable")
        if feature_policy.kill_switch_active:
            reasons.add("feature_policy_kill_switch_active")
        if feature_policy.policy_version != request.expected_feature_policy_version:
            reasons.add("feature_policy_version_mismatch")
        effective_features: dict[str, bool] = {
            name: False for name in feature_policy.flags
        }
        for feature, requested in request.requested_features.items():
            if not isinstance(requested, bool):
                reasons.add("requested_feature_value_invalid")
                continue
            if feature not in feature_policy.flags:
                reasons.add("requested_feature_unknown")
                continue
            if requested and feature_policy.flags.get(feature) is not True:
                reasons.add("requested_feature_not_persistently_enabled")
                continue
            effective_features[feature] = (
                requested and feature_policy.flags.get(feature) is True
            )
        for feature, dependencies in _FEATURE_DEPENDENCIES.items():
            if effective_features.get(feature) and any(
                not effective_features.get(item, False) for item in dependencies
            ):
                reasons.add("feature_dependency_unsatisfied")

        if limits.status != "qualified":
            reasons.add("baseline_limits_not_qualified")
        if limits.policy_version != request.expected_limit_policy_version:
            reasons.add("baseline_limit_policy_version_mismatch")
        if isinstance(limits.participant_cap, bool) or limits.participant_cap <= 0:
            reasons.add("participant_cap_not_positive")
        if not grounding.accepted:
            reasons.add("evidence_not_grounded")

        if reasons:
            return ActivationDecision(
                status="no_go",
                reason_codes=tuple(sorted(reasons)),
                effective_mode="unsupported",
                effective_features=MappingProxyType(
                    {name: False for name in effective_features}
                ),
                participant_cap=0,
                rollback_required=(
                    any(value is True for value in feature_policy.flags.values())
                    or limits.participant_cap > 0
                ),
            )
        return ActivationDecision(
            status="go",
            reason_codes=(),
            effective_mode=runtime.effective_mode,
            effective_features=MappingProxyType(effective_features),
            participant_cap=limits.participant_cap,
            rollback_required=False,
        )


__all__ = [
    "ActivationDecision",
    "ActivationRequest",
    "FeaturePolicySnapshot",
    "LimitsSnapshot",
    "ParentReadinessSnapshot",
    "RuntimeCapabilitySnapshot",
    "SfuBroadcastActivationBoundary",
]
