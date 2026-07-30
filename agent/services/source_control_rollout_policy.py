"""Monotonic feature policy for the additive Source Control Center rollout."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping


class SourceControlRolloutError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 503) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SourceControlRolloutStage(IntEnum):
    SHADOW_READ_MODEL = 0
    PERSISTENT_SOURCES = 1
    WORKSPACE_INDEXING = 2
    LOCAL_GRANTS = 3
    GITHUB = 4
    CLOUD_GRANTS = 5
    LEGACY_DISABLED = 6


@dataclass(frozen=True)
class SourceControlRolloutConfiguration:
    stage: SourceControlRolloutStage
    shadow_compare_enabled: bool
    legacy_aliases_enabled: bool
    production_release_allowed: bool

    def __post_init__(self) -> None:
        if (
            self.stage is SourceControlRolloutStage.LEGACY_DISABLED
            and self.legacy_aliases_enabled
        ):
            raise SourceControlRolloutError(
                "legacy_aliases_conflict_with_disabled_stage"
            )
        if (
            self.stage >= SourceControlRolloutStage.CLOUD_GRANTS
            and not self.production_release_allowed
        ):
            raise SourceControlRolloutError(
                "cloud_grants_require_release_gate"
            )
        if (
            self.stage is SourceControlRolloutStage.SHADOW_READ_MODEL
            and not self.shadow_compare_enabled
        ):
            raise SourceControlRolloutError(
                "shadow_stage_requires_comparison"
            )


@dataclass(frozen=True)
class SourceControlCapabilities:
    shadow_read_model: bool
    persistent_sources: bool
    workspace_indexing: bool
    local_grants: bool
    github: bool
    cloud_grants: bool
    legacy_aliases: bool


class SourceControlRolloutPolicy:
    def __init__(self, config: SourceControlRolloutConfiguration) -> None:
        self._config = config

    def capabilities(self) -> SourceControlCapabilities:
        stage = self._config.stage
        return SourceControlCapabilities(
            shadow_read_model=True,
            persistent_sources=stage
            >= SourceControlRolloutStage.PERSISTENT_SOURCES,
            workspace_indexing=stage
            >= SourceControlRolloutStage.WORKSPACE_INDEXING,
            local_grants=stage >= SourceControlRolloutStage.LOCAL_GRANTS,
            github=stage >= SourceControlRolloutStage.GITHUB,
            cloud_grants=(
                stage >= SourceControlRolloutStage.CLOUD_GRANTS
                and self._config.production_release_allowed
            ),
            legacy_aliases=self._config.legacy_aliases_enabled,
        )

    @property
    def configuration(self) -> SourceControlRolloutConfiguration:
        return self._config

    def require(self, capability: str) -> None:
        capabilities = self.capabilities()
        if capability not in capabilities.__dataclass_fields__:
            raise SourceControlRolloutError("rollout_capability_unknown")
        if not getattr(capabilities, capability):
            raise SourceControlRolloutError(
                f"rollout_capability_disabled:{capability}"
            )


@dataclass(frozen=True)
class ShadowDecisionDifference:
    operation: str
    legacy_decision: str
    canonical_decision: str
    legacy_reason_code: str
    canonical_reason_code: str


class SourceControlShadowComparator:
    """Compare decisions without changing runtime authorization."""

    _ALLOWED_DECISIONS = frozenset(
        {"allow", "deny", "approval_required", "unavailable"}
    )

    def compare(
        self,
        *,
        operation: str,
        legacy: Mapping[str, str],
        canonical: Mapping[str, str],
    ) -> ShadowDecisionDifference | None:
        legacy_decision = str(legacy.get("decision") or "")
        canonical_decision = str(canonical.get("decision") or "")
        if (
            legacy_decision not in self._ALLOWED_DECISIONS
            or canonical_decision not in self._ALLOWED_DECISIONS
        ):
            raise SourceControlRolloutError("shadow_decision_invalid")
        legacy_reason = str(legacy.get("reason_code") or "unspecified")
        canonical_reason = str(canonical.get("reason_code") or "unspecified")
        for value in (operation, legacy_reason, canonical_reason):
            if (
                not value
                or len(value) > 128
                or not all(
                    char.isalnum() or char in "._:-"
                    for char in value
                )
            ):
                raise SourceControlRolloutError("shadow_reason_invalid")
        if (
            legacy_decision == canonical_decision
            and legacy_reason == canonical_reason
        ):
            return None
        return ShadowDecisionDifference(
            operation=operation,
            legacy_decision=legacy_decision,
            canonical_decision=canonical_decision,
            legacy_reason_code=legacy_reason,
            canonical_reason_code=canonical_reason,
        )


@dataclass(frozen=True)
class ShadowProjectionDifference:
    operation: str
    legacy_digest: str
    canonical_digest: str


class SourceControlShadowProjectionComparator:
    """Compare projection digests without retaining projected source data."""

    def compare(
        self,
        *,
        operation: str,
        legacy_digest: str,
        canonical_digest: str,
    ) -> ShadowProjectionDifference | None:
        if not operation or len(operation) > 64 or not all(
            char.isalnum() or char in "._:-" for char in operation
        ):
            raise SourceControlRolloutError("shadow_operation_invalid")
        for digest in (legacy_digest, canonical_digest):
            if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise SourceControlRolloutError(
                    "shadow_projection_digest_invalid"
                )
        if legacy_digest == canonical_digest:
            return None
        return ShadowProjectionDifference(
            operation=operation,
            legacy_digest=legacy_digest,
            canonical_digest=canonical_digest,
        )


@dataclass(frozen=True)
class SourceControlRolloutThresholds:
    minimum_samples: int = 100
    success_max_difference_rate: float = 0.005
    abort_difference_rate: float = 0.02
    rollback_authorization_failure_rate: float = 0.01
    rollback_error_rate: float = 0.02

    def __post_init__(self) -> None:
        if self.minimum_samples < 1:
            raise SourceControlRolloutError("rollout_minimum_samples_invalid")
        rates = (
            self.success_max_difference_rate,
            self.abort_difference_rate,
            self.rollback_authorization_failure_rate,
            self.rollback_error_rate,
        )
        if any(rate < 0 or rate > 1 for rate in rates):
            raise SourceControlRolloutError("rollout_threshold_rate_invalid")
        if self.success_max_difference_rate >= self.abort_difference_rate:
            raise SourceControlRolloutError(
                "rollout_difference_threshold_order_invalid"
            )


@dataclass(frozen=True)
class SourceControlRolloutWindow:
    samples: int
    differences: int
    authorization_failures: int
    errors: int

    def __post_init__(self) -> None:
        values = (
            self.samples,
            self.differences,
            self.authorization_failures,
            self.errors,
        )
        if any(isinstance(value, bool) or value < 0 for value in values):
            raise SourceControlRolloutError("rollout_window_invalid")
        if any(value > self.samples for value in values[1:]):
            raise SourceControlRolloutError("rollout_window_count_invalid")


@dataclass(frozen=True)
class SourceControlRolloutAssessment:
    action: str
    difference_rate: float
    authorization_failure_rate: float
    error_rate: float


def assess_source_control_rollout(
    window: SourceControlRolloutWindow,
    *,
    thresholds: SourceControlRolloutThresholds | None = None,
) -> SourceControlRolloutAssessment:
    limits = thresholds or SourceControlRolloutThresholds()
    denominator = max(window.samples, 1)
    difference_rate = window.differences / denominator
    authorization_rate = window.authorization_failures / denominator
    error_rate = window.errors / denominator
    action = "hold"
    if window.samples >= limits.minimum_samples:
        if (
            authorization_rate
            >= limits.rollback_authorization_failure_rate
            or error_rate >= limits.rollback_error_rate
        ):
            action = "rollback"
        elif difference_rate >= limits.abort_difference_rate:
            action = "abort"
        elif difference_rate <= limits.success_max_difference_rate:
            action = "advance"
    return SourceControlRolloutAssessment(
        action=action,
        difference_rate=difference_rate,
        authorization_failure_rate=authorization_rate,
        error_rate=error_rate,
    )
