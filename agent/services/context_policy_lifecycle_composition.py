"""Composition adapters for the persistent Context Policy lifecycle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Protocol

from agent.repositories.context_policy_lifecycle_repository import (
    SQLContextPolicyLifecycleRepository,
)
from agent.services.context_access_policy_service import (
    ContextAccessPolicyService,
)
from agent.services.context_policy_lifecycle import (
    ContextPolicyActor,
    ContextPolicyDiagnostic,
    ContextPolicyLifecycleError,
    ContextPolicyLifecycleService,
    ContextPolicyPreview,
    ContextPolicyVersion,
    derive_context_policy_digest,
)
from ananta_contracts.context_access_policy import (
    ContextAccessPolicy,
    ContextAccessRule,
    RequestedOperation,
    build_destination_context,
)
from ananta_contracts.source_control import (
    GrantOperation,
    GrantTransformation,
)


class ContextPolicySourceRevisionPort(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_revision_id: str,
    ) -> Mapping[str, Any] | None: ...


class ContextPolicyDestinationPort(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        destination_id: str,
    ) -> Mapping[str, Any] | None: ...


class ExistingContextPolicyLintAdapter:
    """Reuse the established policy parser and validation service."""

    def __init__(self, service: ContextAccessPolicyService) -> None:
        self._service = service

    def lint(
        self,
        *,
        document: Mapping[str, Any],
    ) -> Sequence[ContextPolicyDiagnostic]:
        try:
            policy = _domain_policy(document)
            errors = self._service.validate_policy(policy)
        except (TypeError, ValueError):
            return (
                ContextPolicyDiagnostic(
                    severity="error",
                    reason_code="policy_document_invalid",
                ),
            )
        return tuple(
            ContextPolicyDiagnostic(
                severity="error",
                reason_code="policy_validation_failed",
                rule_id=_diagnostic_rule_id(message),
            )
            for message in errors
        )


class ExistingContextPolicyPreviewAdapter:
    """Resolve real source/destination facts, then reuse domain evaluation."""

    def __init__(
        self,
        *,
        service: ContextAccessPolicyService,
        sources: ContextPolicySourceRevisionPort,
        destinations: ContextPolicyDestinationPort,
    ) -> None:
        self._service = service
        self._sources = sources
        self._destinations = destinations

    def preview(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_document: Mapping[str, Any],
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
    ) -> ContextPolicyPreview:
        source = self._sources.resolve(
            tenant_id=tenant_id,
            project_id=project_id,
            source_revision_id=source_revision_id,
        )
        if source is None:
            raise ContextPolicyLifecycleError(
                "source_revision_not_found"
            )
        destination = self._destinations.resolve(
            tenant_id=tenant_id,
            project_id=project_id,
            destination_id=destination_id,
        )
        if destination is None:
            raise ContextPolicyLifecycleError(
                "destination_not_found"
            )
        try:
            policy = _domain_policy(policy_document)
            context = build_destination_context(
                worker_id=str(destination["worker_id"]),
                worker_kind=str(destination["worker_kind"]),
                runtime_target_id=str(destination["runtime_id"]),
                runtime_kind=str(destination["runtime_kind"]),
                provider_id=str(destination["provider_id"]),
                provider_location=str(
                    destination["provider_location"]
                ),
                model_id=str(destination["model_id"]),
                requested_operation=_requested_operation(operation),
            )
            block = dict(source)
            block["source_revision_id"] = source_revision_id
            block["requested_transformation"] = transformation.value
            decision = self._service.get_decision(
                policy,
                block,
                context,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContextPolicyLifecycleError(
                "policy_preview_input_invalid"
            ) from exc
        raw_decision = getattr(decision, "decision", "deny")
        decision_value = str(
            getattr(raw_decision, "value", raw_decision)
        )
        raw_reason = getattr(decision, "reason_code", None)
        reason = str(getattr(raw_reason, "value", raw_reason or ""))
        matched = getattr(decision, "matched_rule_ids", None)
        if matched is None:
            matched_rule = getattr(decision, "matched_rule_id", None)
            matched = (matched_rule,) if matched_rule else ()
        elif isinstance(matched, str):
            matched = (matched,)
        approval = (
            "required"
            if decision_value == "approval_required"
            else None
        )
        return ContextPolicyPreview(
            decision=decision_value,
            reason_codes=(reason,) if reason else (),
            matched_rule_path=tuple(str(item) for item in matched),
            approval_requirement=approval,
            policy_digest=derive_context_policy_digest(
                policy_document
            ),
        )


class RequiredIdempotencyContextPolicyLifecycleService:
    """HTTP-facing facade: every mutation requires one idempotency key."""

    def __init__(
        self,
        lifecycle: ContextPolicyLifecycleService,
        *,
        clock=lambda: datetime.now(timezone.utc).isoformat(),
    ) -> None:
        self._lifecycle = lifecycle
        self._clock = clock

    def create_draft(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        document: Mapping[str, Any],
        expected_latest_version: int | None,
        idempotency_key: str,
    ) -> ContextPolicyVersion:
        return self._lifecycle.create_draft(
            actor=actor,
            policy_id=policy_id,
            document=document,
            expected_latest_version=expected_latest_version,
            now=self._now(),
            idempotency_key=_required_key(idempotency_key),
        )

    def activate(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str,
    ) -> ContextPolicyVersion:
        return self._lifecycle.activate(
            actor=actor,
            policy_id=policy_id,
            version=version,
            if_match=if_match,
            idempotency_key=_required_key(idempotency_key),
        )

    def revoke(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str,
    ) -> ContextPolicyVersion:
        return self._lifecycle.revoke(
            actor=actor,
            policy_id=policy_id,
            version=version,
            if_match=if_match,
            idempotency_key=_required_key(idempotency_key),
        )

    def rollback(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        target_version: int,
        expected_latest_version: int,
        idempotency_key: str,
    ) -> ContextPolicyVersion:
        return self._lifecycle.rollback(
            actor=actor,
            policy_id=policy_id,
            target_version=target_version,
            expected_latest_version=expected_latest_version,
            now=self._now(),
            idempotency_key=_required_key(idempotency_key),
        )

    def lint(self, **kwargs: Any):
        return self._lifecycle.lint(**kwargs)

    def preview(self, **kwargs: Any):
        return self._lifecycle.preview(**kwargs)

    def versions(self, **kwargs: Any):
        return self._lifecycle.versions(**kwargs)

    def detail(self, **kwargs: Any):
        return self._lifecycle.detail(**kwargs)

    def active(self, **kwargs: Any):
        return self._lifecycle.active(**kwargs)

    def _now(self) -> str:
        value = str(self._clock() or "")
        if not value:
            raise ContextPolicyLifecycleError(
                "policy_clock_invalid"
            )
        return value


def build_persistent_context_policy_lifecycle(
    *,
    engine: Any,
    sources: ContextPolicySourceRevisionPort,
    destinations: ContextPolicyDestinationPort,
    context_policy_service: ContextAccessPolicyService | None = None,
    clock=lambda: datetime.now(timezone.utc).isoformat(),
) -> RequiredIdempotencyContextPolicyLifecycleService:
    domain = context_policy_service or ContextAccessPolicyService()
    repository = SQLContextPolicyLifecycleRepository(
        engine,
        clock=clock,
    )
    lifecycle = ContextPolicyLifecycleService(
        repository=repository,
        lint=ExistingContextPolicyLintAdapter(domain),
        preview=ExistingContextPolicyPreviewAdapter(
            service=domain,
            sources=sources,
            destinations=destinations,
        ),
        audit=repository,
    )
    return RequiredIdempotencyContextPolicyLifecycleService(
        lifecycle,
        clock=clock,
    )


def _domain_policy(
    document: Mapping[str, Any],
) -> ContextAccessPolicy:
    rules = [
        ContextAccessRule(**dict(raw))
        for raw in list(document.get("rules") or [])
        if isinstance(raw, Mapping)
    ]
    if len(rules) != len(list(document.get("rules") or [])):
        raise ValueError("policy_rules_invalid")
    return ContextAccessPolicy(
        policy_id=str(document.get("policy_id") or ""),
        version=1,
        scope=str(document.get("scope") or "project"),
        rules=rules,
        defaults=dict(document.get("defaults") or {}),
        precedence=int(document.get("precedence") or 0),
    )


def _requested_operation(
    operation: GrantOperation,
) -> RequestedOperation:
    mapping = {
        GrantOperation.CHAT_CONTEXT: RequestedOperation.send_to_llm,
        GrantOperation.INDEX: RequestedOperation.send_to_worker,
        GrantOperation.EXPORT: RequestedOperation.tool_read,
    }
    try:
        return mapping[operation]
    except KeyError as exc:
        raise ContextPolicyLifecycleError(
            "policy_operation_not_supported"
        ) from exc


def _diagnostic_rule_id(message: str) -> str | None:
    prefix = str(message or "").partition(":")[0].strip()
    if not prefix.lower().startswith("rule "):
        return None
    value = prefix[5:].strip()
    return value or None


def _required_key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ContextPolicyLifecycleError(
            "policy_idempotency_key_required"
        )
    return key


__all__ = [
    "ContextPolicyDestinationPort",
    "ContextPolicySourceRevisionPort",
    "ExistingContextPolicyLintAdapter",
    "ExistingContextPolicyPreviewAdapter",
    "RequiredIdempotencyContextPolicyLifecycleService",
    "build_persistent_context_policy_lifecycle",
]
