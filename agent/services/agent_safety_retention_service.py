"""Bounded automatic cleanup for forensic agent-safety freezes."""

from __future__ import annotations

import uuid
from typing import Any

from agent.services.agent_safety_ports import SandboxCleanupPort
from agent.services.agent_safety_state_store import AgentSafetyStateStorePort
from ananta_contracts.agent_safety import utc_now


class AgentSafetyRetentionService:
    def __init__(self, store: AgentSafetyStateStorePort, *, cleanup: SandboxCleanupPort) -> None:
        self._store = store
        self._cleanup = cleanup

    def sweep_expired(self, *, now: str | None = None, limit: int = 100) -> dict[str, Any]:
        observed_at = str(now or utc_now())
        bounded = min(max(int(limit), 1), 1_000)
        candidates = [
            run
            for run in self._store.list("run")
            if run.get("state") == "freeze"
            and str(run.get("freeze_expires_at") or "") <= observed_at
            and not run.get("cleanup_completed_at")
        ][:bounded]
        receipts: list[dict[str, Any]] = []
        for run in candidates:
            if not self._store.list("incident_bundle", run_id=str(run["run_id"])):
                continue
            operation_id = f"asr_{uuid.uuid4().hex}"
            run_receipts = [
                self._cleanup.cleanup(
                    operation_id=operation_id,
                    run_id=str(run["run_id"]),
                    sandbox_id=str(agent["sandbox_id"]),
                )
                for agent in list(run.get("agents") or [])
            ]
            enforced = all(receipt.enforced for receipt in run_receipts)
            current = self._store.get("run", str(run["run_id"])) or run
            self._store.append(
                "run",
                str(run["run_id"]),
                {
                    **current,
                    "state": "cleaned" if enforced else "cleanup_failed_closed",
                    "cleanup_completed_at": observed_at if enforced else None,
                    "execution_allowed": False,
                },
                expected_revision=int(current["revision"]),
            )
            receipts.extend(receipt.as_dict() for receipt in run_receipts)
        return {
            "state": "completed",
            "candidate_count": len(candidates),
            "receipt_count": len(receipts),
            "receipts": receipts,
            "human_intervention_required": False,
        }


__all__ = ["AgentSafetyRetentionService"]
