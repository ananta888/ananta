"""Provider-neutral runtime handoff for promoted Unsloth artifacts."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
from agent.services.unsloth_task_port import (
    HubTaskSubmissionPort,
    UnslothAuditPort,
)


class RuntimeHandoffError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_runtime_promotion_binding(
    *,
    record: Any,
    artifact_sha256: str,
    resolved_sha256: str,
    source_ids: tuple[str, ...],
    run_ids: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate immutable promotion bindings without Hub composition imports."""
    if not hmac.compare_digest(resolved_sha256, artifact_sha256):
        raise RuntimeHandoffError(
            "runtime_handoff_export_hash_mismatch",
            "The promoted export hash does not match the Hub artifact.",
        )
    if sorted(source_ids) != sorted(record.source_ids) or sorted(run_ids) != sorted(record.run_ids):
        raise RuntimeHandoffError(
            "runtime_handoff_evidence_binding_mismatch",
            "Trusted evidence IDs do not match the promoted adapter.",
        )
    promotion = dict(record.promotion_history[-1])
    evidence = dict(promotion.get("evidence") or {})
    if (
        promotion.get("schema") != "ananta.adapter-promotion-history.v1"
        or not str(promotion.get("promotion_id") or "")
        or not hmac.compare_digest(
            str(promotion.get("artifact_sha256") or ""),
            str(record.artifact_sha256 or ""),
        )
        or str(evidence.get("adapter_id") or "") != record.adapter_id
        or not hmac.compare_digest(
            str(evidence.get("adapter_sha256") or ""),
            str(record.artifact_sha256 or ""),
        )
        or str(evidence.get("base_model_id") or "") != record.base_model
        or not hmac.compare_digest(str(evidence.get("export_sha256") or ""), artifact_sha256)
    ):
        raise RuntimeHandoffError(
            "runtime_handoff_promotion_binding_mismatch",
            "Runtime artifact is not bound to immutable promotion history.",
        )
    if (
        sorted(evidence.get("source_ids") or []) != sorted(source_ids)
        or sorted(evidence.get("run_ids") or []) != sorted(run_ids)
    ):
        raise RuntimeHandoffError(
            "runtime_handoff_promotion_evidence_mismatch",
            "Runtime evidence does not match immutable promotion history.",
        )
    return promotion, evidence


@dataclass(frozen=True)
class RuntimeArtifact:
    artifact_id: str
    tenant_id: str
    artifact_sha256: str
    registry_state: str
    verification_state: str
    format: str


@dataclass(frozen=True)
class RuntimeHandoffRequest:
    tenant_id: str
    endpoint_id: str
    provider: str
    artifact: RuntimeArtifact
    source_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    expected_endpoint_revision: int
    provider_descriptor: Mapping[str, Any] = field(default_factory=dict)
    endpoint_descriptor: Mapping[str, Any] = field(default_factory=dict)
    api_capabilities: Mapping[str, bool] = field(default_factory=dict)
    limits: Mapping[str, int] = field(default_factory=dict)
    promotion_id: str = ""
    adapter_id: str = ""
    adapter_sha256: str = ""
    base_model_id: str = ""
    base_model_sha256: str = ""
    job_id: str = ""
    attempt_id: str = ""
    fencing_token_digest: str = ""
    reason_sha256: str = ""


@dataclass(frozen=True)
class RuntimeHandoffPlan:
    tenant_id: str
    payload_json: str
    confirmation_digest: str


class UnslothRuntimeHandoffService:
    """Plans and submits deployment work without owning a runtime process."""

    _SHA256 = re.compile(r"^[0-9a-f]{64}$")
    _FORMATS = frozenset({"adapter", "merged_16bit", "gguf"})

    def __init__(
        self,
        *,
        tasks: HubTaskSubmissionPort,
        audit: UnslothAuditPort,
        evidence: ProvidedEvidenceRegistry,
    ) -> None:
        self._tasks = tasks
        self._audit = audit
        self._evidence = evidence

    def plan(self, request: RuntimeHandoffRequest) -> RuntimeHandoffPlan:
        references = self._validate(request)
        payload = {
            "schema_version": 2,
            "tenant_id": request.tenant_id,
            "endpoint_id": request.endpoint_id,
            "provider": request.provider,
            "artifact": {
                "artifact_id": request.artifact.artifact_id,
                "artifact_sha256": request.artifact.artifact_sha256,
                "format": request.artifact.format,
                "promotion_id": request.promotion_id,
                "adapter_id": request.adapter_id,
                "adapter_sha256": request.adapter_sha256,
                "base_model_id": request.base_model_id,
                "base_model_sha256": request.base_model_sha256,
            },
            "provider_descriptor": dict(request.provider_descriptor),
            "endpoint_descriptor": dict(request.endpoint_descriptor),
            "api_capabilities": dict(request.api_capabilities),
            "limits": dict(request.limits),
            "source_ids": list(references.source_ids),
            "run_ids": list(references.run_ids),
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "fencing_token_digest": request.fencing_token_digest,
            "reason_sha256": request.reason_sha256,
            "expected_endpoint_revision": request.expected_endpoint_revision,
            "fallback": None,
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        confirmation = hashlib.sha256(
            f"unsloth-runtime-handoff:{payload_json}".encode()
        ).hexdigest()
        return RuntimeHandoffPlan(
            tenant_id=request.tenant_id,
            payload_json=payload_json,
            confirmation_digest=confirmation,
        )

    def submit(
        self,
        plan: RuntimeHandoffPlan,
        *,
        confirmation_digest: str,
        idempotency_key: str | None = None,
    ) -> str:
        expected = hashlib.sha256(
            f"unsloth-runtime-handoff:{plan.payload_json}".encode()
        ).hexdigest()
        if (
            confirmation_digest != plan.confirmation_digest
            or confirmation_digest != expected
        ):
            raise RuntimeHandoffError(
                "runtime_handoff_confirmation_invalid",
                "The runtime handoff must be explicitly confirmed.",
            )
        payload = json.loads(plan.payload_json)
        task_id = self._tasks.submit(
            task_type="ml.runtime.artifact_handoff",
            tenant_id=plan.tenant_id,
            payload=payload,
            idempotency_key=str(idempotency_key or expected),
        )
        self._audit.record(
            event_type="unsloth.runtime_handoff_submitted",
            tenant_id=plan.tenant_id,
            subject_id=task_id,
            details={
                "endpoint_id": payload["endpoint_id"],
                "provider": payload["provider"],
                "artifact_id": payload["artifact"]["artifact_id"],
                "artifact_sha256": payload["artifact"]["artifact_sha256"],
                "expected_endpoint_revision": payload[
                    "expected_endpoint_revision"
                ],
                "manifest_sha256": hashlib.sha256(
                    plan.payload_json.encode("utf-8")
                ).hexdigest(),
            },
        )
        return task_id

    def _validate(self, request: RuntimeHandoffRequest):
        if (
            not request.tenant_id
            or not request.endpoint_id
            or not request.provider
        ):
            raise RuntimeHandoffError(
                "runtime_handoff_scope_missing",
                "Tenant, endpoint, and provider are required.",
            )
        if request.artifact.tenant_id != request.tenant_id:
            raise RuntimeHandoffError(
                "runtime_handoff_tenant_mismatch",
                "The artifact belongs to a different tenant.",
            )
        if request.artifact.registry_state != "promoted":
            raise RuntimeHandoffError(
                "runtime_handoff_not_promoted",
                "Only promoted artifacts can be handed to a runtime.",
            )
        if request.artifact.verification_state != "verified":
            raise RuntimeHandoffError(
                "runtime_handoff_not_verified",
                "The artifact digest must be verified before handoff.",
            )
        if not self._SHA256.fullmatch(request.artifact.artifact_sha256):
            raise RuntimeHandoffError(
                "runtime_handoff_hash_invalid",
                "The artifact requires a lowercase SHA-256 digest.",
            )
        if request.artifact.format not in self._FORMATS:
            raise RuntimeHandoffError(
                "runtime_handoff_format_unsupported",
                "The runtime cannot consume this artifact format.",
            )
        if request.expected_endpoint_revision < 0:
            raise RuntimeHandoffError(
                "runtime_handoff_revision_invalid",
                "An endpoint revision fence is required.",
            )
        if (
            request.endpoint_descriptor.get("endpoint_id")
            != request.endpoint_id
            or request.provider_descriptor.get("provider_id")
            != request.provider
        ):
            raise RuntimeHandoffError(
                "runtime_handoff_descriptor_binding_mismatch",
                "Provider and endpoint descriptors must match the request.",
            )
        if (
            not request.promotion_id
            or not request.adapter_id
            or not request.base_model_id
            or not request.job_id
            or not request.attempt_id
        ):
            raise RuntimeHandoffError(
                "runtime_handoff_provenance_missing",
                "Immutable promotion provenance is required.",
            )
        for digest in (
            request.adapter_sha256,
            request.base_model_sha256,
            request.fencing_token_digest,
            request.reason_sha256,
        ):
            if not self._SHA256.fullmatch(digest):
                raise RuntimeHandoffError(
                    "runtime_handoff_provenance_hash_invalid",
                    "Runtime handoff provenance requires lowercase SHA-256 digests.",
                )
        if (
            not request.api_capabilities
            or not any(request.api_capabilities.values())
            or not all(
                isinstance(enabled, bool)
                for enabled in request.api_capabilities.values()
            )
        ):
            raise RuntimeHandoffError(
                "runtime_handoff_capabilities_invalid",
                "Runtime API capabilities must be explicit.",
            )
        return self._evidence.resolve(
            source_ids=request.source_ids,
            run_ids=request.run_ids,
        )
