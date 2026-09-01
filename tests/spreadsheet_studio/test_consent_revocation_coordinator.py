from __future__ import annotations

from agent.services.spreadsheet_consent_revocation_coordinator import (
    SpreadsheetConsentRevocationCoordinator,
)


def test_coordinator_requests_idempotent_job_fencing_without_human_gate() -> None:
    calls = []

    class Control:
        def cancel_job(self, principal, job_id, *, idempotency_key, reason):
            calls.append((principal, job_id, idempotency_key, reason))
            return {"id": job_id, "status": "cancel_requested"}

    result = SpreadsheetConsentRevocationCoordinator(control=Control()).reconcile(
        tenant_id="tenant-a",
        impact={
            "impact_id": "revocation-one",
            "training_jobs": [{"job_id": "job-one", "owner_id": "user-a", "state": "fence_required"}],
        },
    )

    assert calls[0][0].tenant_id == "tenant-a"
    assert calls[0][1:] == (
        "job-one",
        "revocation-one-job-one",
        "spreadsheet_consent_revoked",
    )
    assert result["state"] == "quarantined"
    assert result["human_intervention_required"] is False
