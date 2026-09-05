"""Coordinate fully automatic gates under Hub-issued evidence authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from agent.ports.evidence_identity import EvidenceIdentityRegistryPort

GateScope = Literal["test", "local", "external", "production"]
ReleaseScope = Literal["local", "external", "production"]


class HubEvidenceGateError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class EvidenceGateSourceAdmission:
    origin_type: str
    origin_digest: str
    content_digest: str
    policy_digest: str
    synthetic: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceGateRequest:
    tenant_id: str
    project_id: str
    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    repository_revision: str
    input_digest: str
    execution_profile_digest: str
    environment_digest: str
    evidence_scope: GateScope
    required_scope: ReleaseScope
    idempotency_key: str
    sources: tuple[EvidenceGateSourceAdmission, ...]
    synthetic: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceGateOutcome:
    passed: bool
    verified: bool
    reason_code: str
    source_ids: tuple[str, ...]
    run_id: str
    result_digest: str
    execution: Mapping[str, Any]


def canonical_evidence_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HubEvidenceGateError("evidence_gate_result_not_json") from exc
    return hashlib.sha256(encoded).hexdigest()


class HubEvidenceGateService:
    """Own source admission, reservation, result ingress and verification.

    The injected executor is the Worker boundary. It receives only the closed
    assignment projection and returns a content-safe result summary. The Hub
    derives the result digest and is the only component that mutates registry
    state.
    """

    def __init__(self, registry: EvidenceIdentityRegistryPort) -> None:
        self._registry = registry

    def execute(
        self,
        request: EvidenceGateRequest,
        executor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> EvidenceGateOutcome:
        if not request.sources:
            raise HubEvidenceGateError("evidence_gate_sources_required")
        source_ids = tuple(
            self._registry.register_source(
                tenant_id=request.tenant_id,
                project_id=request.project_id,
                origin_type=source.origin_type,
                origin_digest=source.origin_digest,
                content_digest=source.content_digest,
                policy_digest=source.policy_digest,
                evidence_scope=request.evidence_scope,
                synthetic=source.synthetic,
            ).source_id
            for source in request.sources
        )
        run = self._registry.reserve_run(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            task_id=request.task_id,
            assignment_id=request.assignment_id,
            dispatch_lease_id=request.dispatch_lease_id,
            repository_revision=request.repository_revision,
            input_digest=request.input_digest,
            execution_profile_digest=request.execution_profile_digest,
            environment_digest=request.environment_digest,
            source_ids=source_ids,
            evidence_scope=request.evidence_scope,
            idempotency_key=request.idempotency_key,
            synthetic=request.synthetic,
        )
        projection = self._registry.assignment_projection(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            run_id=run.run_id,
            task_id=request.task_id,
            assignment_id=request.assignment_id,
            dispatch_lease_id=request.dispatch_lease_id,
        )
        try:
            execution = self._execution_result(executor(projection))
        except (KeyboardInterrupt, SystemExit) as exc:
            cancellation = {
                "passed": False,
                "reason_code": "evidence_gate_executor_cancelled",
                "error_type": type(exc).__name__,
            }
            self._record(
                request,
                run.run_id,
                "cancelled",
                canonical_evidence_digest(cancellation),
            )
            raise
        except Exception as exc:
            failure = {
                "passed": False,
                "reason_code": "evidence_gate_executor_raised",
                "error_type": type(exc).__name__,
            }
            self._record(request, run.run_id, "failed", canonical_evidence_digest(failure))
            raise
        passed = execution["passed"] is True
        result_digest = canonical_evidence_digest(execution)
        self._record(
            request,
            run.run_id,
            "succeeded" if passed else "failed",
            result_digest,
        )
        verification = self._registry.verify_release_binding(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            run_id=run.run_id,
            required_scope=request.required_scope,
            task_id=request.task_id,
            repository_revision=request.repository_revision,
            source_ids=source_ids,
        )
        return EvidenceGateOutcome(
            passed=passed,
            verified=verification.verified,
            reason_code=verification.reason_code,
            source_ids=source_ids,
            run_id=run.run_id,
            result_digest=result_digest,
            execution=execution,
        )

    def _record(
        self,
        request: EvidenceGateRequest,
        run_id: str,
        terminal_state: Literal["succeeded", "failed", "cancelled"],
        result_digest: str,
    ) -> None:
        self._registry.record_result(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            run_id=run_id,
            assignment_id=request.assignment_id,
            dispatch_lease_id=request.dispatch_lease_id,
            terminal_state=terminal_state,
            result_digest=result_digest,
        )

    @staticmethod
    def _execution_result(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise HubEvidenceGateError("evidence_gate_result_invalid")
        result = dict(value)
        if not isinstance(result.get("passed"), bool):
            raise HubEvidenceGateError("evidence_gate_passed_flag_required")
        canonical_evidence_digest(result)
        return result


_SERVICE: HubEvidenceGateService | None = None


def get_hub_evidence_gate_service() -> HubEvidenceGateService:
    from agent.services.hub_evidence_registry_service import (
        get_hub_evidence_registry_service,
    )

    global _SERVICE
    if _SERVICE is None:
        _SERVICE = HubEvidenceGateService(get_hub_evidence_registry_service())
    return _SERVICE


__all__ = [
    "EvidenceGateOutcome",
    "EvidenceGateRequest",
    "EvidenceGateSourceAdmission",
    "GateScope",
    "HubEvidenceGateError",
    "HubEvidenceGateService",
    "ReleaseScope",
    "canonical_evidence_digest",
    "get_hub_evidence_gate_service",
]
