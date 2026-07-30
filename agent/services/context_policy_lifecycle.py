"""Immutable Context Access Policy lifecycle controlled by the Hub."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from ananta_contracts.source_control import GrantOperation, GrantTransformation


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATES = frozenset({"draft", "active", "superseded", "revoked"})
_DOCUMENT_KEYS = frozenset(
    {"schema", "policy_id", "scope", "defaults", "rules", "precedence"}
)


class ContextPolicyLifecycleError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class ContextPolicyActor:
    subject_id: str
    tenant_id: str
    project_id: str
    roles: frozenset[str]


@dataclass(frozen=True)
class ContextPolicyVersion:
    policy_id: str
    version: int
    tenant_id: str
    project_id: str
    state: str
    document: Mapping[str, Any]
    policy_digest: str
    etag: str
    created_by: str
    created_at: str

    def __post_init__(self) -> None:
        if self.version < 1 or self.state not in _STATES:
            raise ContextPolicyLifecycleError("policy_version_invalid")
        if not _SHA256.fullmatch(self.policy_digest):
            raise ContextPolicyLifecycleError("policy_digest_invalid")
        if not _SHA256.fullmatch(self.etag):
            raise ContextPolicyLifecycleError("policy_etag_invalid")


@dataclass(frozen=True)
class ContextPolicyDiagnostic:
    severity: str
    reason_code: str
    rule_id: str | None = None


@dataclass(frozen=True)
class ContextPolicyPreview:
    decision: str
    reason_codes: tuple[str, ...]
    matched_rule_path: tuple[str, ...]
    approval_requirement: str | None
    policy_digest: str


class ContextPolicyLifecycleRepositoryPort(Protocol):
    def latest(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
    ) -> ContextPolicyVersion | None: ...

    def get_version(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
    ) -> ContextPolicyVersion | None: ...

    def list_versions(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[Sequence[ContextPolicyVersion], str | None]: ...

    def active(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
    ) -> ContextPolicyVersion | None: ...

    def get_mutation_result(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
    ) -> ContextPolicyVersion | None: ...

    def append_draft(
        self,
        *,
        version: ContextPolicyVersion,
        expected_latest_version: int | None,
        operation: str | None = None,
        idempotency_key: str | None = None,
        request_digest: str | None = None,
    ) -> ContextPolicyVersion: ...

    def transition(
        self,
        *,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
        expected_etag: str,
        target_state: str,
        actor_id: str,
        operation: str | None = None,
        idempotency_key: str | None = None,
        request_digest: str | None = None,
    ) -> ContextPolicyVersion: ...


class ContextPolicyLintPort(Protocol):
    def lint(
        self,
        *,
        document: Mapping[str, Any],
    ) -> Sequence[ContextPolicyDiagnostic]: ...


class ContextPolicyPreviewPort(Protocol):
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
    ) -> ContextPolicyPreview: ...


class ContextPolicyAuditPort(Protocol):
    def record(
        self,
        *,
        operation: str,
        actor_id: str,
        tenant_id: str,
        project_id: str,
        policy_id: str,
        version: int,
        policy_digest: str,
        reason_code: str,
    ) -> None: ...


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


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _normalize_etag(value: str) -> str:
    return str(value or "").strip().removeprefix("W/").strip().strip('"')


def derive_context_policy_etag(
    *,
    policy_id: str,
    version: int,
    policy_digest: str,
    state: str,
) -> str:
    return _digest(
        {
            "policy_id": policy_id,
            "version": version,
            "policy_digest": policy_digest,
            "state": state,
        }
    )


def derive_context_policy_digest(
    document: Mapping[str, Any],
) -> str:
    return _digest(dict(document))
