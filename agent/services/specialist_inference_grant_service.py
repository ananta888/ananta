"""Hub entry point for existing reserved identities, never an evidence issuer."""

from __future__ import annotations

from agent.services.specialist_inference_assignment_service import SpecialistInferenceAssignmentService

MODEL_REVISION = "d0a4e53d09cebe6bc963dd9be319d4279084bb2d"


class GrantAuditUnavailable(RuntimeError):
    """No token may leave the Hub when its issuance cannot be audited."""


class SpecialistInferenceGrantService:
    def __init__(self, *, registry, private_key, lease_factory, audit):
        self._registry, self._key, self._leases, self._audit = registry, private_key, lease_factory, audit

    def issue(self, payload):
        required = {"tenant_id", "project_id", "run_id", "task_id", "assignment_id", "dispatch_lease_id", "body"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError("specialist_grant_request_invalid")
        if any(
            not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 256 for key in required - {"body"}
        ) or not isinstance(payload["body"], dict):
            raise ValueError("specialist_grant_request_invalid")
        lease = self._leases(tenant_id=payload["tenant_id"], project_id=payload["project_id"])
        signer = SpecialistInferenceAssignmentService(
            registry=self._registry, private_key=self._key, lease_authority=lease
        )
        token = signer.issue(**payload, model_revision=MODEL_REVISION)
        # Audit must succeed before the token is exposed; no prompt or token is logged.
        try:
            self._audit("specialist_inference_grant_issued", {key: payload[key] for key in required - {"body"}})
        except Exception as error:
            raise GrantAuditUnavailable("specialist_grant_audit_unavailable") from error
        return {"assignment_token": token, "model_revision": MODEL_REVISION, "grants_permission": False}


def get_specialist_inference_grant_service():
    from flask import current_app, has_app_context

    if not has_app_context() or current_app.config.get("ROLE") != "hub":
        raise ValueError("specialist_grant_hub_required")
    configured = current_app.extensions.get("specialist_inference_grants")
    if configured is not None:
        return configured
    import os

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from agent.common.audit import log_audit
    from agent.repositories.specialist_dispatch_lease import SqlSpecialistDispatchLeaseAuthority
    from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service
    from ananta_contracts.file_credentials import read_file_managed_bytes

    path = os.environ.get("ANANTA_SPECIALIST_INFERENCE_SIGNING_KEY_FILE", "")
    if not path:
        raise ValueError("specialist_grant_signing_key_unconfigured")
    key = load_pem_private_key(
        read_file_managed_bytes(path, description="Specialist issuer key", min_bytes=32, max_bytes=16384), password=None
    )
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("specialist_grant_signing_key_invalid")
    return SpecialistInferenceGrantService(
        registry=get_hub_evidence_registry_service(),
        private_key=key,
        lease_factory=SqlSpecialistDispatchLeaseAuthority,
        audit=lambda action, details: log_audit(action, details, raise_errors=True),
    )
