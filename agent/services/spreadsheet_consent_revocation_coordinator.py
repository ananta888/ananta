"""Automatic ML-Intern fencing coordinator for spreadsheet consent revocation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.services.ml_intern_training_contract import MlInternTrainingContractError
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


class SpreadsheetConsentRevocationCoordinator:
    """Reconcile durable impact intent with the existing Hub training control plane."""

    def __init__(self, *, control: Any) -> None:
        self._control = control

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
        return {
            **dict(impact),
            "training_jobs": outcomes,
            "state": "fence_retry_pending" if pending else "quarantined",
            "automatic_reconciliation": True,
            "human_intervention_required": False,
        }


__all__ = ["SpreadsheetConsentRevocationCoordinator"]
