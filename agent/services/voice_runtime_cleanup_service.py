from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Callable, Protocol

from agent.common.audit import log_audit
from agent.repositories.voice_runtime_cleanup import (
    VoiceRuntimeCleanupRecordInput,
    VoiceRuntimeCleanupRepository,
    VoiceRuntimeCleanupStatus,
)
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
    validate_identifier,
    voice_scope_digest,
)
from agent.services.voice_sensitive_text_codec import get_voice_sensitive_text_codec

RuntimeStreamDelete = Callable[[str, str], None]
RestrictedCacheGC = Callable[[VoicePrincipal, str], None]
_STREAM_CLEANUP_OPERATIONS = frozenset(
    {"consent_revoke", "profile_delete", "stream_expire", "stream_orphan"}
)
_CACHE_CLEANUP_OPERATIONS = frozenset({"consent_revoke", "profile_delete"})
_RUNTIME_STREAM_DELETE = "runtime_stream_delete"
_RESTRICTED_CACHE_GC = "restricted_cache_gc"


class RuntimeCleanupTargetCodec(Protocol):
    def encrypt(self, value: str | None) -> str | None: ...

    def decrypt(self, value: str | None) -> str | None: ...


@dataclass(frozen=True)
class VoiceRuntimeCleanupTarget:
    source_session_id: str
    runtime_session_id: str


@dataclass(frozen=True)
class VoiceRuntimeCleanupRun:
    attempted_count: int
    succeeded_count: int
    status: VoiceRuntimeCleanupStatus

    def public(self) -> dict[str, int | bool]:
        return {
            "runtime_cleanup_pending": self.status.pending_count > 0,
            "runtime_cleanup_failed_count": self.status.failed_count,
        }


class VoiceRuntimeCleanupService:
    """Durably stage and execute content-free runtime stream deletion work."""

    def __init__(
        self,
        repository: VoiceRuntimeCleanupRepository | None = None,
        codec: RuntimeCleanupTargetCodec | None = None,
        runtime_stream_delete: RuntimeStreamDelete | None = None,
        restricted_cache_gc: RestrictedCacheGC | None = None,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._repository = repository or VoiceRuntimeCleanupRepository()
        self._codec = codec or get_voice_sensitive_text_codec()
        self._runtime_stream_delete = runtime_stream_delete or self._delete_runtime_stream
        self._restricted_cache_gc = restricted_cache_gc or self._gc_restricted_cache
        self._audit = audit_sink

    def stage(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        operation: str,
        targets: tuple[VoiceRuntimeCleanupTarget, ...],
        provisional: bool = False,
    ) -> int:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        if operation not in _STREAM_CLEANUP_OPERATIONS:
            raise VoiceGovernanceError(
                code="voice_runtime_cleanup.invalid_operation",
                message="voice runtime cleanup operation is invalid",
                status_code=500,
            )
        records: list[VoiceRuntimeCleanupRecordInput] = []
        for target in targets:
            source_session_id = validate_identifier(
                target.source_session_id,
                field="source_session_id",
                max_length=200,
            )
            runtime_session_id = validate_identifier(
                target.runtime_session_id,
                field="runtime_session_id",
                max_length=200,
            )
            ciphertext = self._codec.encrypt(runtime_session_id)
            if not ciphertext:
                raise VoiceGovernanceError(
                    code="voice_runtime_cleanup.encryption_failed",
                    message="voice runtime cleanup target could not be encrypted",
                    status_code=500,
                )
            records.append(
                VoiceRuntimeCleanupRecordInput(
                    source_session_id=source_session_id,
                    cleanup_kind=_RUNTIME_STREAM_DELETE,
                    runtime_session_ciphertext=ciphertext,
                    target_digest=hashlib.sha256(runtime_session_id.encode("utf-8")).hexdigest(),
                    initial_state="provisional" if provisional else "pending",
                )
            )
        return int(
            self._repository.stage_many(
                principal,
                profile_id=normalized_profile_id,
                operation=operation,
                targets=records,
            )
        )

    def stage_cache_gc(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        operation: str,
    ) -> int:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        if operation not in _CACHE_CLEANUP_OPERATIONS:
            raise VoiceGovernanceError(
                code="voice_runtime_cleanup.invalid_operation",
                message="voice runtime cleanup operation is invalid",
                status_code=500,
            )
        return int(
            self._repository.stage_many(
                principal,
                profile_id=normalized_profile_id,
                operation=operation,
                targets=(
                    VoiceRuntimeCleanupRecordInput(
                        source_session_id="restricted-cache-gc",
                        cleanup_kind=_RESTRICTED_CACHE_GC,
                    ),
                ),
            )
        )

    def retry_profile(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        include_provisional: bool = False,
    ) -> VoiceRuntimeCleanupRun:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        records = self._repository.list_scope(principal, normalized_profile_id)
        if not include_provisional:
            records = tuple(record for record in records if record.state != "provisional")
        return self._retry_records(principal, normalized_profile_id, records)

    def retry_target(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        source_session_id: str,
        *,
        include_provisional: bool = False,
    ) -> VoiceRuntimeCleanupRun:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        normalized_source_id = validate_identifier(
            source_session_id,
            field="source_session_id",
            max_length=200,
        )
        record = self._repository.get_source(principal, normalized_profile_id, normalized_source_id)
        records = (
            (record,)
            if record is not None and (include_provisional or record.state != "provisional")
            else ()
        )
        return self._retry_records(principal, normalized_profile_id, records)

    def activate_target(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        source_session_id: str,
        *,
        operation: str,
    ) -> bool:
        if operation not in _STREAM_CLEANUP_OPERATIONS:
            raise VoiceGovernanceError(
                code="voice_runtime_cleanup.invalid_operation",
                message="voice runtime cleanup operation is invalid",
                status_code=500,
            )
        return self._repository.activate_source(
            principal,
            validate_identifier(profile_id, field="profile_id"),
            validate_identifier(source_session_id, field="source_session_id", max_length=200),
            operation=operation,
        )

    def cancel_target(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        source_session_id: str,
    ) -> bool:
        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        record = self._repository.get_source(
            principal,
            normalized_profile_id,
            validate_identifier(source_session_id, field="source_session_id", max_length=200),
        )
        return bool(
            record is not None
            and self._repository.complete(principal, normalized_profile_id, record.id)
        )

    def _retry_records(
        self,
        principal: VoicePrincipal,
        normalized_profile_id: str,
        records,
        *,
        audit: bool = True,
    ) -> VoiceRuntimeCleanupRun:
        attempted_count = 0
        succeeded_count = 0
        for record in records:
            if not self._repository.mark_attempt(principal, normalized_profile_id, record.id):
                continue
            attempted_count += 1
            if record.cleanup_kind == _RESTRICTED_CACHE_GC:
                if record.operation not in _CACHE_CLEANUP_OPERATIONS:
                    self._repository.mark_failed(
                        principal,
                        normalized_profile_id,
                        record.id,
                        reason_code="runtime_cleanup_target_unreadable",
                    )
                    continue
                try:
                    self._restricted_cache_gc(
                        principal,
                        self._cache_request_id(principal, normalized_profile_id, record.operation),
                    )
                except Exception:
                    self._repository.mark_failed(
                        principal,
                        normalized_profile_id,
                        record.id,
                        reason_code="restricted_cache_gc_failed",
                    )
                    continue
                if self._repository.complete(principal, normalized_profile_id, record.id):
                    succeeded_count += 1
                continue
            try:
                if (
                    record.cleanup_kind != _RUNTIME_STREAM_DELETE
                    or record.operation not in _STREAM_CLEANUP_OPERATIONS
                ):
                    raise VoiceGovernanceError(
                        code="voice_runtime_cleanup.invalid_stored_operation",
                        message="stored voice runtime cleanup operation is invalid",
                        status_code=500,
                    )
                runtime_session_id = self._codec.decrypt(record.runtime_session_ciphertext)
                runtime_session_id = validate_identifier(
                    runtime_session_id,
                    field="runtime_session_id",
                    max_length=200,
                )
                if not self._target_matches_digest(runtime_session_id, record.target_digest):
                    raise VoiceGovernanceError(
                        code="voice_runtime_cleanup.target_integrity_failed",
                        message="voice runtime cleanup target failed integrity validation",
                        status_code=500,
                    )
            except Exception:
                self._repository.mark_failed(
                    principal,
                    normalized_profile_id,
                    record.id,
                    reason_code="runtime_cleanup_target_unreadable",
                )
                continue
            try:
                self._runtime_stream_delete(
                    runtime_session_id,
                    self._request_id(record.operation, record.source_session_id),
                )
            except Exception:
                self._repository.mark_failed(
                    principal,
                    normalized_profile_id,
                    record.id,
                    reason_code="runtime_delete_failed",
                )
                continue
            if self._repository.complete(principal, normalized_profile_id, record.id):
                succeeded_count += 1
        status = self._repository.status(principal, normalized_profile_id)
        if audit:
            self._audit(
                "voice_runtime_cleanup_processed",
                {
                    "scope_digest": voice_scope_digest(principal, normalized_profile_id),
                    "attempted_count": attempted_count,
                    "succeeded_count": succeeded_count,
                    "failed_count": status.failed_count,
                    "pending": status.pending_count > 0,
                },
            )
        return VoiceRuntimeCleanupRun(
            attempted_count=attempted_count,
            succeeded_count=succeeded_count,
            status=status,
        )

    def retry_all_pending(
        self,
        *,
        scope_limit: int = 100,
        include_provisional: bool = False,
    ) -> int:
        scopes = self._repository.list_pending_scopes(
            limit=scope_limit,
            include_provisional=include_provisional,
        )
        attempted_count = 0
        succeeded_count = 0
        failed_count = 0
        pending_scope_count = 0
        for principal, profile_id in scopes:
            records = self._repository.list_scope(principal, profile_id)
            if not include_provisional:
                records = tuple(
                    record for record in records if record.state != "provisional"
                )
            run = self._retry_records(
                principal,
                profile_id,
                records,
                audit=False,
            )
            attempted_count += run.attempted_count
            succeeded_count += run.succeeded_count
            failed_count += run.status.failed_count
            pending_scope_count += int(run.status.pending_count > 0)
        if scopes:
            self._audit(
                "voice_runtime_cleanup_batch_processed",
                {
                    "scope_count": len(scopes),
                    "attempted_count": attempted_count,
                    "succeeded_count": succeeded_count,
                    "failed_count": failed_count,
                    "pending_scope_count": pending_scope_count,
                },
            )
        return len(scopes)

    def retry_pseudonymous_profile(
        self,
        principal: VoicePrincipal,
        profile_id: str,
    ) -> VoiceRuntimeCleanupRun:
        replacement_principal, replacement_profile_id = self._pseudonymous_scope(principal, profile_id)
        return self.retry_profile(replacement_principal, replacement_profile_id)

    def pseudonymize_profile_scope(self, principal: VoicePrincipal, profile_id: str) -> int:
        """Detach pending cleanup retries from deleted user identifiers."""

        normalized_profile_id = validate_identifier(profile_id, field="profile_id")
        replacement_principal, replacement_profile_id = self._pseudonymous_scope(
            principal,
            normalized_profile_id,
        )
        return self._repository.pseudonymize_scope(
            principal,
            normalized_profile_id,
            replacement_principal=replacement_principal,
            replacement_profile_id=replacement_profile_id,
        )

    @staticmethod
    def _pseudonymous_scope(
        principal: VoicePrincipal,
        profile_id: str,
    ) -> tuple[VoicePrincipal, str]:
        scope_digest = voice_scope_digest(principal, profile_id)
        suffix = scope_digest[:32]
        return (
            VoicePrincipal(
                tenant_id=f"voice-cleanup-{suffix}",
                subject="hub-privacy-cleanup",
            ),
            f"deleted-scope-{suffix}",
        )

    @staticmethod
    def _target_matches_digest(runtime_session_id: str, expected_digest: str) -> bool:
        actual_digest = hashlib.sha256(runtime_session_id.encode("utf-8")).hexdigest()
        return hmac.compare_digest(actual_digest, expected_digest)

    @staticmethod
    def _request_id(operation: str, source_session_id: str) -> str:
        prefix = {
            "profile_delete": "privacy-delete",
            "consent_revoke": "consent-revoke",
            "stream_expire": "stream-expire",
            "stream_orphan": "stream-orphan",
        }.get(operation, "stream-cleanup")
        return f"{prefix}-{source_session_id}"

    @staticmethod
    def _cache_request_id(principal: VoicePrincipal, profile_id: str, operation: str) -> str:
        scope_digest = voice_scope_digest(principal, profile_id)
        return f"voice-{operation}-cache-gc-{scope_digest[:24]}"

    @staticmethod
    def _delete_runtime_stream(runtime_session_id: str, request_id: str) -> None:
        from agent.services.voice_provider import get_voice_provider_service

        get_voice_provider_service().delete_stream(
            runtime_session_id=runtime_session_id,
            request_id=request_id,
        )

    @staticmethod
    def _gc_restricted_cache(principal: VoicePrincipal, request_id: str) -> None:
        from agent.services.restricted_inference_management_service import (
            get_restricted_inference_management_service,
        )
        from agent.services.restricted_inference_management_task_service import (
            get_restricted_inference_management_task_service,
        )

        del principal
        management = get_restricted_inference_management_service()
        system_principal = VoicePrincipal(
            tenant_id=f"privacy-cleanup-{hashlib.sha256(request_id.encode()).hexdigest()[:24]}",
            subject="hub-privacy-cleanup",
        )
        get_restricted_inference_management_task_service().execute(
            system_principal,
            operation="model_cache_gc",
            target_id="runtime-cache",
            request_id=request_id,
            callback=management.cache_gc,
        )


voice_runtime_cleanup_service = VoiceRuntimeCleanupService()


def get_voice_runtime_cleanup_service() -> VoiceRuntimeCleanupService:
    return voice_runtime_cleanup_service
