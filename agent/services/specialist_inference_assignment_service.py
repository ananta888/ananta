"""Hub signs only Registry-reserved exact task/assignment/lease projections."""

from __future__ import annotations

import time
from typing import Protocol

from ananta_contracts.specialist_inference_assignment import input_digest, profile_digest, seal_assignment


class InferenceEvidenceRegistry(Protocol):
    def assignment_projection(self, **bindings) -> dict: ...


class CurrentDispatchLeaseAuthority(Protocol):
    def require_current(self, *, task_id: str, assignment_id: str, dispatch_lease_id: str) -> float: ...


class SpecialistInferenceAssignmentService:
    def __init__(
        self,
        *,
        registry: InferenceEvidenceRegistry,
        private_key,
        clock=time.time,
        lease_authority: CurrentDispatchLeaseAuthority | None = None,
    ):
        self._registry, self._key, self._clock = registry, private_key, clock
        self._leases = lease_authority

    def issue(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        task_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        body: dict,
        model_revision: str,
        ttl_seconds: int = 60,
    ) -> str:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ValueError("specialist_assignment_ttl_invalid")
        digest = input_digest(body)
        evidence = self._registry.assignment_projection(
            tenant_id=tenant_id,
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            assignment_id=assignment_id,
            dispatch_lease_id=dispatch_lease_id,
            input_digest=digest,
            execution_profile_digest=profile_digest(model_revision),
        )
        if self._leases is None and evidence["evidence_scope"] not in {"test", "synthetic"}:
            raise ValueError("specialist_current_dispatch_lease_authority_required")
        if self._leases is not None:
            expiry = self._leases.require_current(
                task_id=task_id, assignment_id=assignment_id, dispatch_lease_id=dispatch_lease_id
            )
            if not self._clock() < expiry:
                raise ValueError("specialist_dispatch_lease_expired")
            ttl_seconds = min(ttl_seconds, expiry - self._clock())
        return seal_assignment(
            {
                "schema": "ananta.specialist-inference-assignment.v1",
                "tenant_id": tenant_id,
                "project_id": project_id,
                "evidence": evidence,
                "input_digest": digest,
                "model_revision": model_revision,
                "expires_at": self._clock() + ttl_seconds,
            },
            self._key,
        )
