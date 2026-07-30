"""Effective source access preview, matrix, and dispatch verification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from ananta_contracts.source_control import (
    DestinationDescriptor,
    GrantOperation,
    GrantTransformation,
)


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECISIONS = frozenset(
    {"allow", "deny", "approval_required", "unavailable"}
)


class EffectiveSourceAccessError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class EffectiveSourceRevision:
    source_revision_id: str
    tenant_id: str
    project_id: str
    source_type: str
    sensitivity: str
    revision_digest: str


class EffectiveSourceCatalogPort(Protocol):
    def get_revision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
    ) -> EffectiveSourceRevision | None: ...

    def list_revisions(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> tuple[Sequence[EffectiveSourceRevision], str | None]: ...


class EffectiveDestinationCatalogPort(Protocol):
    def get_destination(
        self,
        *,
        destination_id: str,
    ) -> DestinationDescriptor | None: ...

    def list_destinations(
        self,
        *,
        cursor: str | None,
        limit: int,
        filters: Mapping[str, str],
    ) -> tuple[Sequence[DestinationDescriptor], str | None]: ...


@dataclass(frozen=True)
class EffectivePolicyEvaluation:
    decision: str
    reason_codes: tuple[str, ...]
    matched_rule_path: tuple[str, ...]
    default_applied: bool
    approval_requirement: str | None
    policy_digest: str

    def __post_init__(self) -> None:
        if self.decision not in _DECISIONS:
            raise EffectiveSourceAccessError("policy_decision_invalid")
        if not _SHA256.fullmatch(self.policy_digest):
            raise EffectiveSourceAccessError("policy_digest_invalid")
        for reason in self.reason_codes:
            if not _OPAQUE_ID.fullmatch(reason):
                raise EffectiveSourceAccessError("reason_code_invalid")
        for rule_id in self.matched_rule_path:
            if not _OPAQUE_ID.fullmatch(rule_id):
                raise EffectiveSourceAccessError("rule_path_invalid")
        if (
            self.approval_requirement is not None
            and not _OPAQUE_ID.fullmatch(self.approval_requirement)
        ):
            raise EffectiveSourceAccessError("approval_requirement_invalid")


class EffectiveSourcePolicyPort(Protocol):
    def evaluate(
        self,
        *,
        source_revision: EffectiveSourceRevision,
        destination: DestinationDescriptor,
        operation: GrantOperation,
        transformation: GrantTransformation,
        purpose: str,
    ) -> EffectivePolicyEvaluation: ...


@dataclass(frozen=True)
class EffectiveSourceAccessDecision:
    schema: str
    source_revision_id: str
    revision_digest: str
    destination_id: str
    operation: str
    transformation: str
    purpose: str
    decision: str
    reason_codes: tuple[str, ...]
    matched_rule_path: tuple[str, ...]
    default_applied: bool
    approval_requirement: str | None
    policy_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "source_revision_id": self.source_revision_id,
            "revision_digest": self.revision_digest,
            "destination_id": self.destination_id,
            "operation": self.operation,
            "transformation": self.transformation,
            "purpose": self.purpose,
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "matched_rule_path": list(self.matched_rule_path),
            "default_applied": self.default_applied,
            "approval_requirement": self.approval_requirement,
            "policy_digest": self.policy_digest,
        }


class EffectiveSourceAccessService:
    """One evaluator path for UI preview, matrix, and real dispatch."""

    def __init__(
        self,
        *,
        sources: EffectiveSourceCatalogPort,
        destinations: EffectiveDestinationCatalogPort,
        policy: EffectiveSourcePolicyPort,
    ) -> None:
        self._sources = sources
        self._destinations = destinations
        self._policy = policy

    def preview(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
        purpose: str,
    ) -> EffectiveSourceAccessDecision:
        source = self._resolve_source(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
        )
        destination = self._resolve_destination(destination_id)
        return self._evaluate(
            source=source,
            destination=destination,
            operation=operation,
            transformation=transformation,
            purpose=purpose,
        )

    def verify_dispatch(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
        purpose: str,
        expected_revision_digest: str,
        expected_policy_digest: str,
    ) -> EffectiveSourceAccessDecision:
        decision = self.preview(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
            destination_id=destination_id,
            operation=operation,
            transformation=transformation,
            purpose=purpose,
        )
        if decision.revision_digest != expected_revision_digest:
            raise EffectiveSourceAccessError(
                "source_revision_changed_after_preview"
            )
        if decision.policy_digest != expected_policy_digest:
            raise EffectiveSourceAccessError("policy_changed_after_preview")
        if decision.decision != "allow":
            raise EffectiveSourceAccessError(
                f"dispatch_{decision.decision}"
            )
        return decision

    def matrix(
        self,
        *,
        tenant_id: str,
        project_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
        purpose: str,
        source_cursor: str | None = None,
        destination_cursor: str | None = None,
        source_limit: int = 25,
        destination_limit: int = 25,
        source_filters: Mapping[str, str] | None = None,
        destination_filters: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        if (
            source_limit < 1
            or source_limit > 50
            or destination_limit < 1
            or destination_limit > 50
            or source_limit * destination_limit > 625
        ):
            raise EffectiveSourceAccessError("matrix_limit_invalid")
        normalized_source_filters = _filters(
            source_filters or {},
            allowed={"source_type", "sensitivity", "project_id"},
        )
        normalized_destination_filters = _filters(
            destination_filters or {},
            allowed={
                "worker_id",
                "worker_kind",
                "runtime_kind",
                "provider_id",
                "model_id",
                "model_class",
                "provider_location",
                "data_residency",
            },
        )
        sources, next_source_cursor = self._sources.list_revisions(
            tenant_id=tenant_id,
            project_id=project_id,
            cursor=source_cursor,
            limit=source_limit,
            filters=normalized_source_filters,
        )
        destinations, next_destination_cursor = (
            self._destinations.list_destinations(
                cursor=destination_cursor,
                limit=destination_limit,
                filters=normalized_destination_filters,
            )
        )
        rows: list[dict[str, object]] = []
        for source in sources:
            if (
                source.tenant_id != tenant_id
                or source.project_id != project_id
            ):
                raise EffectiveSourceAccessError("matrix_source_scope_mismatch")
            for destination in destinations:
                rows.append(
                    self._evaluate(
                        source=source,
                        destination=destination,
                        operation=operation,
                        transformation=transformation,
                        purpose=purpose,
                    ).to_dict()
                )
        return {
            "schema": "ananta.source-control.access-matrix.v1",
            "rows": rows,
            "next_source_cursor": next_source_cursor,
            "next_destination_cursor": next_destination_cursor,
        }

    def _resolve_source(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
    ) -> EffectiveSourceRevision:
        source = self._sources.get_revision(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
        )
        if (
            source is None
            or source.tenant_id != tenant_id
            or source.project_id != project_id
        ):
            raise EffectiveSourceAccessError("source_revision_not_found")
        if not _SHA256.fullmatch(source.revision_digest):
            raise EffectiveSourceAccessError("source_revision_digest_invalid")
        return source

    def _resolve_destination(
        self,
        destination_id: str,
    ) -> DestinationDescriptor:
        destination = self._destinations.get_destination(
            destination_id=destination_id
        )
        if destination is None or destination.destination_id != destination_id:
            raise EffectiveSourceAccessError("destination_not_found")
        return destination

    def _evaluate(
        self,
        *,
        source: EffectiveSourceRevision,
        destination: DestinationDescriptor,
        operation: GrantOperation,
        transformation: GrantTransformation,
        purpose: str,
    ) -> EffectiveSourceAccessDecision:
        if not _OPAQUE_ID.fullmatch(str(purpose or "")):
            raise EffectiveSourceAccessError("purpose_invalid")
        evaluation = self._policy.evaluate(
            source_revision=source,
            destination=destination,
            operation=operation,
            transformation=transformation,
            purpose=purpose,
        )
        return EffectiveSourceAccessDecision(
            schema="ananta.source-control.access-decision.v1",
            source_revision_id=source.source_revision_id,
            revision_digest=source.revision_digest,
            destination_id=destination.destination_id,
            operation=operation.value,
            transformation=transformation.value,
            purpose=purpose,
            decision=evaluation.decision,
            reason_codes=evaluation.reason_codes,
            matched_rule_path=evaluation.matched_rule_path,
            default_applied=evaluation.default_applied,
            approval_requirement=evaluation.approval_requirement,
            policy_digest=evaluation.policy_digest,
        )


def _filters(
    values: Mapping[str, str],
    *,
    allowed: set[str],
) -> dict[str, str]:
    if set(values) - allowed:
        raise EffectiveSourceAccessError("matrix_filter_invalid")
    result: dict[str, str] = {}
    for key, value in values.items():
        text = str(value).strip()
        if not text or len(text) > 128:
            raise EffectiveSourceAccessError("matrix_filter_value_invalid")
        result[key] = text
    return result
