from types import SimpleNamespace

import pytest

from agent.services.artifact_visibility_policy import (
    is_artifact_visible_on_generic_surfaces,
    repository_artifact_reference_candidates,
)


@pytest.mark.parametrize(
    "artifact_kind",
    ["knowledge_index_job_payload", "knowledge_index_worker_output"],
)
def test_system_managed_knowledge_index_artifacts_are_not_generic(
    artifact_kind: str,
) -> None:
    artifact = SimpleNamespace(
        artifact_metadata={"system_artifact_kind": artifact_kind}
    )

    assert is_artifact_visible_on_generic_surfaces(artifact) is False


def test_generic_artifact_visibility_fails_closed_for_malformed_metadata() -> None:
    assert is_artifact_visible_on_generic_surfaces(None) is False
    assert (
        is_artifact_visible_on_generic_surfaces(
            SimpleNamespace(artifact_metadata=["not", "a", "mapping"])
        )
        is False
    )
    assert (
        is_artifact_visible_on_generic_surfaces(
            SimpleNamespace(artifact_metadata={"kind": "user_upload"})
        )
        is True
    )


def test_repository_artifact_reference_candidates_support_direct_and_prefixed_ids() -> None:
    assert repository_artifact_reference_candidates("artifact:item-1") == (
        "artifact:item-1",
        "item-1",
    )
    assert repository_artifact_reference_candidates("item-1") == ("item-1",)
