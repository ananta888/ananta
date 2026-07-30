from __future__ import annotations

import pytest

from agent.services.source_control_rollout_policy import (
    SourceControlRolloutConfiguration,
    SourceControlRolloutError,
    SourceControlRolloutPolicy,
    SourceControlRolloutStage,
    SourceControlShadowComparator,
)


def test_rollout_capabilities_are_monotonic() -> None:
    previous_count = 0
    for stage in SourceControlRolloutStage:
        release_allowed = stage >= SourceControlRolloutStage.CLOUD_GRANTS
        policy = SourceControlRolloutPolicy(
            SourceControlRolloutConfiguration(
                stage=stage,
                shadow_compare_enabled=True,
                legacy_aliases_enabled=(
                    stage is not SourceControlRolloutStage.LEGACY_DISABLED
                ),
                production_release_allowed=release_allowed,
            )
        )
        capabilities = policy.capabilities()
        enabled_count = sum(
            bool(getattr(capabilities, name))
            for name in (
                "persistent_sources",
                "workspace_indexing",
                "local_grants",
                "github",
                "cloud_grants",
            )
        )
        assert enabled_count >= previous_count
        previous_count = enabled_count


def test_cloud_grants_require_completed_release_gate() -> None:
    with pytest.raises(
        SourceControlRolloutError,
        match="release_gate",
    ):
        SourceControlRolloutConfiguration(
            stage=SourceControlRolloutStage.CLOUD_GRANTS,
            shadow_compare_enabled=True,
            legacy_aliases_enabled=True,
            production_release_allowed=False,
        )


def test_legacy_disable_and_aliases_are_mutually_exclusive() -> None:
    with pytest.raises(SourceControlRolloutError, match="legacy_aliases"):
        SourceControlRolloutConfiguration(
            stage=SourceControlRolloutStage.LEGACY_DISABLED,
            shadow_compare_enabled=True,
            legacy_aliases_enabled=True,
            production_release_allowed=True,
        )


def test_shadow_comparison_never_returns_runtime_allowance() -> None:
    difference = SourceControlShadowComparator().compare(
        operation="index",
        legacy={"decision": "allow", "reason_code": "legacy_default"},
        canonical={"decision": "deny", "reason_code": "admission_required"},
    )

    assert difference is not None
    assert difference.canonical_decision == "deny"
    assert not hasattr(difference, "effective_decision")
