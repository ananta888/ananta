from __future__ import annotations

import hashlib

import pytest

from agent.services.organization_category_run_evidence_service import (
    OrganizationCategoryRunEvidenceError,
    OrganizationCategoryRunEvidenceService,
)


def test_hub_materializes_only_the_reserved_assignment_bound_run() -> None:
    raw_output = '{"status":"researched"}'
    digest = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()

    catalog = OrganizationCategoryRunEvidenceService().build_catalog(
        task_id="task-1",
        assignment_id="assignment-1",
        dispatch_lease_id="lease-1",
        worker_id="worker-1",
        raw_output=raw_output,
        raw_output_digest=digest,
        allowed_run_refs={"RUN_0001"},
        runtime_artifact_hashes={"prompt": "a" * 64},
    )

    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["source_id"] == "RUN_0001"
    assert entry["source_type"] == "tool_run"
    assert entry["task_id"] == "task-1"
    assert entry["allowed_for_llm_scope"] is True
    assert entry["evidence_binding"]["assignment_id"] == "assignment-1"
    assert entry["evidence_binding"]["dispatch_lease_id"] == "lease-1"
    assert entry["evidence_binding"]["worker_id"] == "worker-1"
    assert len(entry["evidence_binding"]["binding_digest"]) == 64


@pytest.mark.parametrize(
    ("refs", "digest", "reason"),
    [
        ({"RUN_9999"}, None, "category_run_evidence_reservation_invalid"),
        ({"RUN_0001"}, "0" * 64, "category_run_evidence_output_digest_mismatch"),
    ],
)
def test_hub_rejects_unreserved_or_digest_mismatched_run_evidence(
    refs: set[str],
    digest: str | None,
    reason: str,
) -> None:
    raw_output = '{"status":"researched"}'
    actual_digest = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()

    with pytest.raises(OrganizationCategoryRunEvidenceError, match=reason):
        OrganizationCategoryRunEvidenceService().build_catalog(
            task_id="task-1",
            assignment_id="assignment-1",
            dispatch_lease_id="lease-1",
            worker_id="worker-1",
            raw_output=raw_output,
            raw_output_digest=digest or actual_digest,
            allowed_run_refs=refs,
        )
