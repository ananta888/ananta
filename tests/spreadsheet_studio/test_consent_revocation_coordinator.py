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


def test_coordinator_deprecates_and_unloads_impacted_adapters_automatically() -> None:
    calls = []

    class Control:
        def cancel_job(self, *_args, **_kwargs):
            raise AssertionError("no active training job")

    class Runtime:
        def rollback(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "deprecated",
                "rollback_target": {"type": "base_model_only", "base_model": "local/base"},
                "cache_unload": {"status": "unloaded", "reason_code": "adapter_unloaded"},
            }

    result = SpreadsheetConsentRevocationCoordinator(control=Control(), runtime=Runtime()).reconcile(
        tenant_id="tenant-a",
        impact={
            "impact_id": "revocation-two",
            "training_jobs": [],
            "adapters": [{"adapter_id": "adapter-one", "owner_id": "user-a"}],
        },
    )

    assert calls == [
        {
            "adapter_id": "adapter-one",
            "reason": "spreadsheet consent revoked; quarantine adapter and unload runtime cache",
            "tenant_id": "tenant-a",
            "owner_subject": "user-a",
            "expected_version": None,
        }
    ]
    assert result["state"] == "quarantined"
    assert result["adapters"][0]["state"] == "deprecated"
    assert result["adapters"][0]["cache_unload"]["status"] == "unloaded"


def test_coordinator_discovers_adapters_from_revoked_dataset_without_human_gate() -> None:
    calls = []

    class Control:
        def cancel_job(self, *_args, **_kwargs):
            raise AssertionError("no active training job")

    class Runtime:
        def quarantine_dataset_adapters(self, **kwargs):
            calls.append(kwargs)
            return [
                {
                    "adapter_id": "adapter-derived",
                    "status": "deprecated",
                    "rollback_target": {"type": "base_model_only", "base_model": "local/base"},
                    "cache_unload": {"status": "unloaded", "reason_code": "adapter_unloaded"},
                }
            ]

    digest = "d" * 64
    result = SpreadsheetConsentRevocationCoordinator(control=Control(), runtime=Runtime()).reconcile(
        tenant_id="tenant-a",
        impact={
            "impact_id": "revocation-three",
            "owner_id": "user-a",
            "training_jobs": [],
            "dataset_digests": [digest],
            "adapters": [],
        },
    )

    assert calls == [
        {
            "dataset_hashes": [digest],
            "reason": "spreadsheet consent revoked; quarantine adapter and unload runtime cache",
            "tenant_id": "tenant-a",
            "owner_subject": "user-a",
        }
    ]
    assert result["state"] == "quarantined"
    assert result["adapters"][0]["adapter_id"] == "adapter-derived"
    assert result["human_intervention_required"] is False


def test_coordinator_records_automatic_retry_when_adapter_runtime_is_unavailable() -> None:
    class Control:
        def cancel_job(self, *_args, **_kwargs):
            raise AssertionError("no active training job")

    result = SpreadsheetConsentRevocationCoordinator(control=Control()).reconcile(
        tenant_id="tenant-a",
        impact={
            "impact_id": "revocation-four",
            "owner_id": "user-a",
            "training_jobs": [],
            "dataset_digests": ["d" * 64],
            "adapters": [],
        },
    )

    assert result["state"] == "fence_retry_pending"
    assert result["automatic_reconciliation"] is True
    assert result["human_intervention_required"] is False
