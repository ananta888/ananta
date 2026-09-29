from __future__ import annotations

from agent.artifacts.artifact_access_policy import ArtifactAccessPolicy


def test_policy_allows_local_worker_for_project_private() -> None:
    decision = ArtifactAccessPolicy().evaluate(
        goal_id="goal-1",
        artifact_sensitivity="internal",
        requested_usage="use_as_context",
        worker_kind="native",
        provider_location="local",
        data_boundary="project_private",
    )
    assert decision.decision == "allow"
    assert decision.reason_code == "allowed"


def test_policy_denies_local_only_for_cloud_provider() -> None:
    decision = ArtifactAccessPolicy().evaluate(
        goal_id="goal-1",
        artifact_sensitivity="internal",
        requested_usage="read",
        worker_kind="general",
        provider_location="cloud",
        data_boundary="local_only",
    )
    assert decision.decision == "deny"
    assert decision.reason_code == "data_boundary_local_only"


def test_policy_denies_approved_cloud_without_explicit_policy() -> None:
    decision = ArtifactAccessPolicy().evaluate(
        goal_id="goal-1",
        artifact_sensitivity="internal",
        requested_usage="read",
        worker_kind="general",
        provider_location="cloud",
        data_boundary="approved_cloud",
    )
    assert decision.decision == "deny"
    assert decision.reason_code == "approved_cloud_requires_explicit_policy"


def test_policy_allows_approved_cloud_with_explicit_policy() -> None:
    decision = ArtifactAccessPolicy().evaluate(
        goal_id="goal-1",
        artifact_sensitivity="internal",
        requested_usage="read",
        worker_kind="general",
        provider_location="cloud",
        data_boundary="approved_cloud",
        allow_approved_cloud=True,
    )
    assert decision.decision == "allow"
    assert decision.reason_code == "allowed"


def test_policy_denies_unknown_artifact_and_unknown_usage() -> None:
    unknown_artifact = ArtifactAccessPolicy().evaluate(
        goal_id="goal-1",
        artifact_sensitivity="mystery",
        requested_usage="read",
        worker_kind="general",
        provider_location="local",
        data_boundary="project_private",
    )
    unknown_usage = ArtifactAccessPolicy().evaluate(
        goal_id="goal-1",
        artifact_sensitivity="internal",
        requested_usage="execute",
        worker_kind="general",
        provider_location="local",
        data_boundary="project_private",
    )
    assert unknown_artifact.decision == "deny"
    assert unknown_artifact.reason_code == "unknown_artifact_sensitivity"
    assert unknown_usage.decision == "deny"
    assert unknown_usage.reason_code == "unknown_requested_usage"
