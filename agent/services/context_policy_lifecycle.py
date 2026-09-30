"""Immutable Context Access Policy lifecycle controlled by the Hub.

Value types and digests live in ``agent.models.context_policy_lifecycle`` and
ports in ``agent.ports.context_policy_lifecycle``; both are re-exported here.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from agent.models.context_policy_lifecycle import (
    ContextPolicyActor,
    ContextPolicyDiagnostic,
    ContextPolicyLifecycleError,
    ContextPolicyPreview,
    ContextPolicyVersion,
    derive_context_policy_digest,
    derive_context_policy_etag,
)
from agent.models.context_policy_lifecycle import (
    context_policy_payload_digest as _digest,
)
from agent.ports.context_policy_lifecycle import (
    ContextPolicyAuditPort,
    ContextPolicyLifecycleRepositoryPort,
    ContextPolicyLintPort,
    ContextPolicyPreviewPort,
)
from ananta_contracts.source_control import GrantOperation, GrantTransformation

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_DOCUMENT_KEYS = frozenset(
    {"schema", "policy_id", "scope", "defaults", "rules", "precedence"}
)


class ContextPolicyLifecycleService:
    def __init__(
        self,
        *,
        repository: ContextPolicyLifecycleRepositoryPort,
        lint: ContextPolicyLintPort,
        preview: ContextPolicyPreviewPort,
        audit: ContextPolicyAuditPort,
    ) -> None:
        self._repository = repository
        self._lint = lint
        self._preview = preview
        self._audit = audit

    def create_draft(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        document: Mapping[str, Any],
        expected_latest_version: int | None,
        now: str,
        idempotency_key: str | None = None,
        _mutation_operation: str = "draft",
        _mutation_context: Mapping[str, Any] | None = None,
    ) -> ContextPolicyVersion:
        _require_mutator(actor)
        _validate_id("policy_id", policy_id)
        normalized = _normalize_document(policy_id, document)
        request_digest = _digest(
            {
                "operation": _mutation_operation,
                "tenant_id": actor.tenant_id,
                "project_id": actor.project_id,
                "actor_id": actor.subject_id,
                "policy_id": policy_id,
                "expected_latest_version": expected_latest_version,
                "document": normalized,
                "context": dict(_mutation_context or {}),
            }
        )
        replay = self._mutation_replay(
            actor=actor,
            policy_id=policy_id,
            operation=_mutation_operation,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay
        latest = self._repository.latest(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            policy_id=policy_id,
        )
        actual_latest = latest.version if latest is not None else None
        if actual_latest != expected_latest_version:
            raise ContextPolicyLifecycleError("policy_version_conflict")
        version = (actual_latest or 0) + 1
        digest = derive_context_policy_digest(normalized)
        etag = derive_context_policy_etag(
            policy_id=policy_id,
            version=version,
            policy_digest=digest,
            state="draft",
        )
        candidate = ContextPolicyVersion(
            policy_id=policy_id,
            version=version,
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            state="draft",
            document=normalized,
            policy_digest=digest,
            etag=etag,
            created_by=actor.subject_id,
            created_at=now,
        )
        append_kwargs = {
            "version": candidate,
            "expected_latest_version": expected_latest_version,
        }
        if idempotency_key is not None:
            append_kwargs.update(
                {
                    "operation": _mutation_operation,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                }
            )
        saved = self._repository.append_draft(**append_kwargs)
        self._audit_version(
            saved,
            actor=actor,
            operation=_mutation_operation,
            reason_code=(
                "policy_rollback_draft_created"
                if _mutation_operation == "rollback"
                else "policy_draft_created"
            ),
        )
        return saved

    def lint(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
    ) -> tuple[ContextPolicyDiagnostic, ...]:
        policy = self._required(actor, policy_id, version)
        diagnostics = tuple(self._lint.lint(document=policy.document))
        for diagnostic in diagnostics:
            if (
                diagnostic.severity not in {"error", "warning", "info"}
                or not _OPAQUE_ID.fullmatch(diagnostic.reason_code)
                or (
                    diagnostic.rule_id is not None
                    and not _OPAQUE_ID.fullmatch(diagnostic.rule_id)
                )
            ):
                raise ContextPolicyLifecycleError("policy_diagnostic_invalid")
        return diagnostics

    def preview(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
        source_revision_id: str,
        destination_id: str,
        operation: GrantOperation,
        transformation: GrantTransformation,
    ) -> ContextPolicyPreview:
        policy = self._required(actor, policy_id, version)
        if policy.state not in {"draft", "active"}:
            raise ContextPolicyLifecycleError(
                "policy_version_not_usable"
            )
        for name, value in (
            ("source_revision_id", source_revision_id),
            ("destination_id", destination_id),
        ):
            _validate_id(name, value)
        result = self._preview.preview(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            policy_document=policy.document,
            source_revision_id=source_revision_id,
            destination_id=destination_id,
            operation=operation,
            transformation=transformation,
        )
        if result.policy_digest != policy.policy_digest:
            raise ContextPolicyLifecycleError("policy_preview_digest_mismatch")
        return result

    def activate(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str | None = None,
    ) -> ContextPolicyVersion:
        _require_mutator(actor)
        request_digest = _digest(
            {
                "operation": "activate",
                "tenant_id": actor.tenant_id,
                "project_id": actor.project_id,
                "actor_id": actor.subject_id,
                "policy_id": policy_id,
                "version": version,
                "if_match": _normalize_etag(if_match),
            }
        )
        replay = self._mutation_replay(
            actor=actor,
            policy_id=policy_id,
            operation="activate",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay
        policy = self._required(actor, policy_id, version)
        if policy.state != "draft":
            raise ContextPolicyLifecycleError("policy_draft_required")
        if _normalize_etag(if_match) != policy.etag:
            raise ContextPolicyLifecycleError("policy_version_conflict")
        diagnostics = self.lint(
            actor=actor,
            policy_id=policy_id,
            version=version,
        )
        if any(item.severity == "error" for item in diagnostics):
            raise ContextPolicyLifecycleError("policy_lint_failed")
        return self._transition(
            actor=actor,
            policy=policy,
            if_match=if_match,
            target_state="active",
            operation="activate",
            reason_code="policy_activated",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )

    def revoke(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str | None = None,
    ) -> ContextPolicyVersion:
        _require_mutator(actor)
        request_digest = _digest(
            {
                "operation": "revoke",
                "tenant_id": actor.tenant_id,
                "project_id": actor.project_id,
                "actor_id": actor.subject_id,
                "policy_id": policy_id,
                "version": version,
                "if_match": _normalize_etag(if_match),
            }
        )
        replay = self._mutation_replay(
            actor=actor,
            policy_id=policy_id,
            operation="revoke",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if replay is not None:
            return replay
        policy = self._required(actor, policy_id, version)
        if policy.state != "active":
            raise ContextPolicyLifecycleError("active_policy_required")
        return self._transition(
            actor=actor,
            policy=policy,
            if_match=if_match,
            target_state="revoked",
            operation="revoke",
            reason_code="policy_revoked",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )

    def rollback(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        target_version: int,
        expected_latest_version: int,
        now: str,
        idempotency_key: str | None = None,
    ) -> ContextPolicyVersion:
        _require_mutator(actor)
        target = self._required(actor, policy_id, target_version)
        if target.state not in {"active", "superseded", "revoked"}:
            raise ContextPolicyLifecycleError("rollback_target_invalid")
        return self.create_draft(
            actor=actor,
            policy_id=policy_id,
            document=target.document,
            expected_latest_version=expected_latest_version,
            now=now,
            idempotency_key=idempotency_key,
            _mutation_operation="rollback",
            _mutation_context={"target_version": target_version},
        )

    def detail(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
    ) -> ContextPolicyVersion:
        return self._required(actor, policy_id, version)

    def active(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
    ) -> ContextPolicyVersion:
        _validate_actor(actor)
        _validate_id("policy_id", policy_id)
        policy = self._repository.active(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            policy_id=policy_id,
        )
        if policy is None or policy.state != "active":
            raise ContextPolicyLifecycleError(
                "active_policy_snapshot_missing"
            )
        return policy

    def versions(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ContextPolicyVersion, ...], str | None]:
        _validate_actor(actor)
        if limit < 1 or limit > 200:
            raise ContextPolicyLifecycleError("policy_limit_invalid")
        versions, next_cursor = self._repository.list_versions(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            policy_id=policy_id,
            cursor=cursor,
            limit=limit,
        )
        if any(
            item.tenant_id != actor.tenant_id
            or item.project_id != actor.project_id
            for item in versions
        ):
            raise ContextPolicyLifecycleError("policy_scope_mismatch")
        return tuple(versions), next_cursor

    def _required(
        self,
        actor: ContextPolicyActor,
        policy_id: str,
        version: int,
    ) -> ContextPolicyVersion:
        _validate_actor(actor)
        _validate_id("policy_id", policy_id)
        if version < 1:
            raise ContextPolicyLifecycleError("policy_version_invalid")
        policy = self._repository.get_version(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            policy_id=policy_id,
            version=version,
        )
        if policy is None:
            raise ContextPolicyLifecycleError("policy_not_found")
        return policy

    def _transition(
        self,
        *,
        actor: ContextPolicyActor,
        policy: ContextPolicyVersion,
        if_match: str,
        target_state: str,
        operation: str,
        reason_code: str,
        idempotency_key: str | None,
        request_digest: str,
    ) -> ContextPolicyVersion:
        normalized_etag = _normalize_etag(if_match)
        if normalized_etag != policy.etag:
            raise ContextPolicyLifecycleError("policy_version_conflict")
        transition_kwargs = dict(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            policy_id=policy.policy_id,
            version=policy.version,
            expected_etag=policy.etag,
            target_state=target_state,
            actor_id=actor.subject_id,
        )
        if idempotency_key is not None:
            transition_kwargs.update(
                {
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                }
            )
        transitioned = self._repository.transition(**transition_kwargs)
        self._audit_version(
            transitioned,
            actor=actor,
            operation=operation,
            reason_code=reason_code,
        )
        return transitioned

    def _mutation_replay(
        self,
        *,
        actor: ContextPolicyActor,
        policy_id: str,
        operation: str,
        idempotency_key: str | None,
        request_digest: str,
    ) -> ContextPolicyVersion | None:
        if idempotency_key is None:
            return None
        _validate_id("idempotency_key", idempotency_key)
        return self._repository.get_mutation_result(
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            policy_id=policy_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )

    def _audit_version(
        self,
        policy: ContextPolicyVersion,
        *,
        actor: ContextPolicyActor,
        operation: str,
        reason_code: str,
    ) -> None:
        self._audit.record(
            operation=operation,
            actor_id=actor.subject_id,
            tenant_id=actor.tenant_id,
            project_id=actor.project_id,
            policy_id=policy.policy_id,
            version=policy.version,
            policy_digest=policy.policy_digest,
            reason_code=reason_code,
        )


def _normalize_document(
    policy_id: str,
    document: Mapping[str, Any],
) -> dict[str, Any]:
    if set(document) - _DOCUMENT_KEYS:
        raise ContextPolicyLifecycleError("policy_document_fields_invalid")
    normalized = dict(document)
    claimed_policy_id = str(normalized.get("policy_id") or policy_id)
    if claimed_policy_id != policy_id:
        raise ContextPolicyLifecycleError("policy_identity_mismatch")
    normalized["policy_id"] = policy_id
    normalized["schema"] = str(
        normalized.get("schema") or "ananta.context-access-policy.v1"
    )
    normalized["scope"] = "project"
    try:
        encoded = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContextPolicyLifecycleError("policy_document_invalid") from exc
    if len(encoded) > 1024 * 1024:
        raise ContextPolicyLifecycleError("policy_document_too_large")
    return json.loads(encoded.decode("utf-8"))


def _require_mutator(actor: ContextPolicyActor) -> None:
    _validate_actor(actor)
    if not {"admin", "project_owner"} & actor.roles:
        raise ContextPolicyLifecycleError("policy_mutation_forbidden")


def _validate_actor(actor: ContextPolicyActor) -> None:
    for name in ("subject_id", "tenant_id", "project_id"):
        _validate_id(name, getattr(actor, name))


def _validate_id(name: str, value: str) -> None:
    if not _OPAQUE_ID.fullmatch(str(value or "")):
        raise ContextPolicyLifecycleError(f"{name}_invalid")


def _normalize_etag(value: str) -> str:
    return str(value or "").strip().removeprefix("W/").strip().strip('"')


__all__ = [
    "ContextPolicyActor",
    "ContextPolicyAuditPort",
    "ContextPolicyDiagnostic",
    "ContextPolicyLifecycleError",
    "ContextPolicyLifecycleRepositoryPort",
    "ContextPolicyLifecycleService",
    "ContextPolicyLintPort",
    "ContextPolicyPreview",
    "ContextPolicyPreviewPort",
    "ContextPolicyVersion",
    "derive_context_policy_digest",
    "derive_context_policy_etag",
]
