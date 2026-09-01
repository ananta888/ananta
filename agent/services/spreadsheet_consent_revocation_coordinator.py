"""Automatic ML-Intern fencing coordinator for spreadsheet consent revocation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.ml_intern_training_contract import MlInternTrainingContractError
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


class SpreadsheetConsentRevocationCoordinator:
    """Reconcile durable impact intent with the existing Hub training control plane."""

    def __init__(self, *, control: Any, runtime: Any | None = None) -> None:
        self._control = control
        self._runtime = runtime

    def reconcile(self, *, tenant_id: str, impact: Mapping[str, Any]) -> dict[str, Any]:
        outcomes = []
        pending = False
        for item in list(impact.get("training_jobs") or []):
            principal = MlInternTrainingPrincipal(tenant_id=tenant_id, subject=str(item["owner_id"]))
            job_id = str(item["job_id"])
            try:
                job = self._control.cancel_job(
                    principal,
                    job_id,
                    idempotency_key=f"{impact['impact_id']}-{job_id}",
                    reason="spreadsheet_consent_revoked",
                )
            except MlInternTrainingContractError as exc:
                if exc.code == "job_not_cancellable":
                    outcomes.append(
                        {
                            "job_id": job_id,
                            "state": "terminal_lineage_quarantined",
                            "reason_code": "spreadsheet_training_job_terminal",
                        }
                    )
                else:
                    pending = True
                    outcomes.append(
                        {
                            "job_id": job_id,
                            "state": "fence_retry_pending",
                            "reason_code": f"spreadsheet_training_fence_{exc.code}",
                        }
                    )
            except (RuntimeError, TimeoutError):
                pending = True
                outcomes.append(
                    {
                        "job_id": job_id,
                        "state": "fence_retry_pending",
                        "reason_code": "spreadsheet_training_fence_unavailable",
                    }
                )
            else:
                outcomes.append(
                    {
                        "job_id": job_id,
                        "state": str(job.get("status") or job.get("state") or "cancel_requested"),
                        "reason_code": "spreadsheet_training_fence_requested",
                    }
                )
        adapter_outcomes = []
        processed_adapter_ids = set()
        dataset_digests = list(impact.get("dataset_digests") or [])
        if dataset_digests:
            if self._runtime is None:
                pending = True
            else:
                try:
                    quarantined = self._runtime.quarantine_dataset_adapters(
                        dataset_hashes=dataset_digests,
                        reason="spreadsheet consent revoked; quarantine adapter and unload runtime cache",
                        tenant_id=tenant_id,
                        owner_subject=str(impact["owner_id"]),
                    )
                except (RuntimeError, ValueError):
                    pending = True
                else:
                    for result in quarantined:
                        adapter_id = str(result["adapter_id"])
                        processed_adapter_ids.add(adapter_id)
                        adapter_outcomes.append(
                            {
                                "adapter_id": adapter_id,
                                "state": str(result.get("status") or "deprecated"),
                                "rollback_target": dict(result.get("rollback_target") or {}),
                                "cache_unload": dict(result.get("cache_unload") or {}),
                                "reason_code": "spreadsheet_adapter_quarantined",
                            }
                        )
        for item in list(impact.get("adapters") or []):
            adapter_id = str(item["adapter_id"])
            if adapter_id in processed_adapter_ids:
                continue
            if self._runtime is None:
                pending = True
                adapter_outcomes.append(
                    {
                        "adapter_id": adapter_id,
                        "state": "quarantine_retry_pending",
                        "reason_code": "spreadsheet_adapter_runtime_unavailable",
                    }
                )
                continue
            try:
                result = self._runtime.rollback(
                    adapter_id=adapter_id,
                    reason="spreadsheet consent revoked; quarantine adapter and unload runtime cache",
                    tenant_id=tenant_id,
                    owner_subject=str(item["owner_id"]),
                    expected_version=None,
                )
            except (RuntimeError, ValueError):
                pending = True
                adapter_outcomes.append(
                    {
                        "adapter_id": adapter_id,
                        "state": "quarantine_retry_pending",
                        "reason_code": "spreadsheet_adapter_quarantine_unavailable",
                    }
                )
            else:
                adapter_outcomes.append(
                    {
                        "adapter_id": adapter_id,
                        "state": str(result.get("status") or "deprecated"),
                        "rollback_target": dict(result.get("rollback_target") or {}),
                        "cache_unload": dict(result.get("cache_unload") or {}),
                        "reason_code": "spreadsheet_adapter_quarantined",
                    }
                )
        return {
            **dict(impact),
            "training_jobs": outcomes,
            "adapters": adapter_outcomes,
            "state": "fence_retry_pending" if pending else "quarantined",
            "automatic_reconciliation": True,
            "human_intervention_required": False,
        }


__all__ = ["SpreadsheetConsentRevocationCoordinator"]
