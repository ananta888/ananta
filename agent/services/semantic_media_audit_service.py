"""Idempotent, content-free audit events for authoritative Hub transitions."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Mapping, Protocol

from agent.services.semantic_media_program_evidence import FORBIDDEN_KEY_FRAGMENTS, assert_content_free

MAX_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
MIN_RETENTION_MS = 60 * 60 * 1000
MAX_PAGE_SIZE = 100
MAX_SCOPE_EVENTS = 10_000
REFERENCE_FIELDS = ("contract_ref", "lease_ref", "job_ref")
AUDIT_EVENT_TYPES = frozenset(
    {
        "semantic_budget",
        "semantic_admission",
        "semantic_consent",
        "semantic_contract",
        "semantic_fallback",
        "semantic_job",
        "semantic_lease",
        "semantic_recovery",
        "semantic_rekey",
        "semantic_relay",
        "speech_adapter",
        "speech_dataset",
        "speech_evidence",
        "speech_training",
    }
)


class SemanticMediaAuditError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class SemanticMediaAuditEvent:
    event_id: str
    idempotency_digest: str
    tenant_digest: str
    scope_digest: str
    event_type: str
    transition: str
    reason_code: str
    epoch: int
    contract_ref: str | None
    lease_ref: str | None
    job_ref: str | None
    created_at_ms: int
    expires_at_ms: int

    def public(self) -> dict[str, object]:
        result = asdict(self)
        result.pop("idempotency_digest", None)
        assert_content_free(result)
        return result


class SemanticMediaAuditRepository(Protocol):
    def append_once(self, event: SemanticMediaAuditEvent) -> tuple[SemanticMediaAuditEvent, bool]: ...

    def page(
        self,
        *,
        tenant_digest: str,
        scope_digest: str,
        after_event_id: str | None,
        limit: int,
        now_ms: int,
    ) -> tuple[tuple[SemanticMediaAuditEvent, ...], str | None]: ...

    def delete_expired(self, *, now_ms: int, limit: int) -> int: ...

    def delete_scope(self, *, tenant_digest: str, scope_digest: str, limit: int) -> int: ...

    def delete_tenant(self, *, tenant_digest: str, limit: int) -> int: ...


class SemanticMediaAuditPort(Protocol):
    """Narrow write-only port used by Hub domain services (DIP/ISP)."""

    def record_transition(
        self,
        *,
        idempotency_key: str,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> tuple[SemanticMediaAuditEvent, bool]: ...

    def prepare_transition(
        self,
        *,
        idempotency_key: str,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> SemanticMediaAuditEvent: ...

    def append_prepared(
        self,
        event: SemanticMediaAuditEvent,
    ) -> tuple[SemanticMediaAuditEvent, bool]: ...


class InMemorySemanticMediaAuditRepository:
    """Thread-safe deterministic adapter for tests and single-process development."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_idempotency: dict[str, SemanticMediaAuditEvent] = {}
        self._events: list[SemanticMediaAuditEvent] = []

    def append_once(self, event: SemanticMediaAuditEvent) -> tuple[SemanticMediaAuditEvent, bool]:
        with self._lock:
            previous = self._by_idempotency.get(event.idempotency_digest)
            if previous is not None:
                if not same_idempotent_audit_request(previous, event):
                    raise SemanticMediaAuditError("audit_idempotency_conflict", status_code=409)
                return previous, False
            scope_count = sum(
                row.tenant_digest == event.tenant_digest and row.scope_digest == event.scope_digest
                for row in self._events
            )
            if scope_count >= MAX_SCOPE_EVENTS:
                raise SemanticMediaAuditError("audit_scope_cardinality_exceeded", status_code=429)
            self._events.append(event)
            self._by_idempotency[event.idempotency_digest] = event
            return event, True

    def page(
        self,
        *,
        tenant_digest: str,
        scope_digest: str,
        after_event_id: str | None,
        limit: int,
        now_ms: int,
    ) -> tuple[tuple[SemanticMediaAuditEvent, ...], str | None]:
        with self._lock:
            rows = [
                row
                for row in self._events
                if row.tenant_digest == tenant_digest
                and row.scope_digest == scope_digest
                and row.expires_at_ms > now_ms
            ]
            start = 0
            if after_event_id is not None:
                positions = [index for index, row in enumerate(rows) if row.event_id == after_event_id]
                if not positions:
                    raise SemanticMediaAuditError("audit_cursor_invalid", status_code=400)
                start = positions[0] + 1
            page = tuple(rows[start : start + limit])
            next_cursor = page[-1].event_id if start + len(page) < len(rows) and page else None
            return page, next_cursor

    def delete_expired(self, *, now_ms: int, limit: int) -> int:
        with self._lock:
            expired = {row.event_id for row in self._events if row.expires_at_ms <= now_ms}
            selected = set(sorted(expired)[:limit])
            if not selected:
                return 0
            self._events = [row for row in self._events if row.event_id not in selected]
            self._by_idempotency = {
                key: row for key, row in self._by_idempotency.items() if row.event_id not in selected
            }
            return len(selected)

    def delete_scope(self, *, tenant_digest: str, scope_digest: str, limit: int) -> int:
        with self._lock:
            selected = {
                row.event_id
                for row in self._events
                if row.tenant_digest == tenant_digest and row.scope_digest == scope_digest
            }
            selected = set(sorted(selected)[:limit])
            return self._delete_ids(selected)

    def delete_tenant(self, *, tenant_digest: str, limit: int) -> int:
        with self._lock:
            selected = {row.event_id for row in self._events if row.tenant_digest == tenant_digest}
            selected = set(sorted(selected)[:limit])
            return self._delete_ids(selected)

    def _delete_ids(self, selected: set[str]) -> int:
        if not selected:
            return 0
        self._events = [row for row in self._events if row.event_id not in selected]
        self._by_idempotency = {
            key: row for key, row in self._by_idempotency.items() if row.event_id not in selected
        }
        return len(selected)


class SemanticMediaAuditService:
    def __init__(
        self,
        repository: SemanticMediaAuditRepository,
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._repository = repository
        self._clock_ms = clock_ms

    def record_transition(
        self,
        *,
        idempotency_key: str,
        tenant_digest: str,
        scope_digest: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> tuple[SemanticMediaAuditEvent, bool]:
        event = self.prepare_transition(
            idempotency_key=idempotency_key,
            tenant_digest=tenant_digest,
            scope_digest=scope_digest,
            event_type=event_type,
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            contract_ref=contract_ref,
            lease_ref=lease_ref,
            job_ref=job_ref,
            retention_ms=retention_ms,
        )
        return self._repository.append_once(event)

    def prepare_transition(
        self,
        *,
        idempotency_key: str,
        tenant_digest: str,
        scope_digest: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> SemanticMediaAuditEvent:
        """Validate and deterministically bind one content-free audit command.

        This method is deliberately side-effect free. Domain repositories use
        the returned value to stage an outbox row inside the same transaction
        as their authoritative mutation.
        """

        for value in (tenant_digest, scope_digest):
            _digest(value, "audit_scope_digest_invalid")
        for value in (event_type, transition, reason_code):
            _identifier(value, "audit_transition_invalid")
            if any(fragment in value.casefold() for fragment in FORBIDDEN_KEY_FRAGMENTS):
                raise SemanticMediaAuditError("audit_transition_invalid")
        if event_type not in AUDIT_EVENT_TYPES:
            raise SemanticMediaAuditError("audit_event_type_invalid")
        if not 1 <= epoch <= 2_147_483_647:
            raise SemanticMediaAuditError("audit_epoch_invalid")
        if not MIN_RETENTION_MS <= retention_ms <= MAX_RETENTION_MS:
            raise SemanticMediaAuditError("audit_retention_invalid")
        refs = {"contract_ref": contract_ref, "lease_ref": lease_ref, "job_ref": job_ref}
        if not any(refs.values()):
            raise SemanticMediaAuditError("audit_authority_ref_required")
        for value in refs.values():
            if value is not None:
                _digest(value, "audit_authority_ref_invalid")
        if not 8 <= len(idempotency_key) <= 256 or any(character.isspace() for character in idempotency_key):
            raise SemanticMediaAuditError("audit_idempotency_key_invalid")
        now_ms = int(self._clock_ms())
        body = {
            "tenant_digest": tenant_digest,
            "scope_digest": scope_digest,
            "event_type": event_type,
            "transition": transition,
            "reason_code": reason_code,
            "epoch": epoch,
            **refs,
            "created_at_ms": now_ms,
            "expires_at_ms": now_ms + retention_ms,
        }
        idempotency_digest = _sha256(
            _canonical(
                {
                    "key": idempotency_key,
                    "tenant_digest": tenant_digest,
                    "scope_digest": scope_digest,
                    "event_type": event_type,
                }
            )
        )
        # The identifier is stable across retries even when a later retry has a
        # different wall-clock timestamp. First-write timestamps remain in the
        # persisted event and are intentionally excluded from the command ID.
        event_binding = {
            "idempotency_digest": idempotency_digest,
            "tenant_digest": tenant_digest,
            "scope_digest": scope_digest,
            "event_type": event_type,
            "transition": transition,
            "reason_code": reason_code,
            "epoch": epoch,
            **refs,
        }
        event_id = f"audit-{_sha256(_canonical(event_binding))[:32]}"
        event = SemanticMediaAuditEvent(
            event_id=event_id,
            idempotency_digest=idempotency_digest,
            **body,
        )
        assert_content_free(event.public())
        return event

    def append_prepared(
        self,
        event: SemanticMediaAuditEvent,
    ) -> tuple[SemanticMediaAuditEvent, bool]:
        """Compatibility path for domains without a transactional repository."""

        assert_content_free(event.public())
        return self._repository.append_once(event)

    def delete_expired(self, *, limit: int = 1000) -> int:
        if not 1 <= limit <= 10_000:
            raise SemanticMediaAuditError("audit_cleanup_limit_invalid")
        return self._repository.delete_expired(now_ms=int(self._clock_ms()), limit=limit)


class SemanticMediaAuditRecorder:
    """Purpose-separated pseudonymization facade for domain services and APIs."""

    def __init__(self, service: SemanticMediaAuditService, *, secret: bytes) -> None:
        if len(secret) < 32:
            raise SemanticMediaAuditError(
                "audit_digest_key_invalid",
                status_code=503,
            )
        self._service = service
        # One-way key separation avoids using the Flask signing key directly.
        self._digest_key = hmac.new(
            secret,
            b"ananta.semantic-media.audit.digest-key.v1",
            hashlib.sha256,
        ).digest()

    def digest(self, kind: str, value: str) -> str:
        _identifier(kind, "audit_digest_kind_invalid")
        if not value or len(value.encode("utf-8")) > 1024:
            raise SemanticMediaAuditError("audit_digest_value_invalid")
        return hmac.new(
            self._digest_key,
            f"{kind}\0{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def record_transition(
        self,
        *,
        idempotency_key: str,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> tuple[SemanticMediaAuditEvent, bool]:
        event = self.prepare_transition(
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            scope=scope,
            event_type=event_type,
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            contract_ref=contract_ref,
            lease_ref=lease_ref,
            job_ref=job_ref,
            retention_ms=retention_ms,
        )
        return self._service.append_prepared(event)

    def prepare_transition(
        self,
        *,
        idempotency_key: str,
        tenant_id: str,
        scope: str,
        event_type: str,
        transition: str,
        reason_code: str,
        epoch: int,
        contract_ref: str | None = None,
        lease_ref: str | None = None,
        job_ref: str | None = None,
        retention_ms: int = 7 * 24 * 60 * 60 * 1000,
    ) -> SemanticMediaAuditEvent:
        key = str(idempotency_key or "")
        if not 8 <= len(key) <= 4096 or any(character.isspace() for character in key):
            raise SemanticMediaAuditError("audit_idempotency_key_invalid")
        if len(key) > 256:
            key = "audit-command:" + hmac.new(
                self._digest_key,
                f"idempotency\0{key}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        return self._service.prepare_transition(
            idempotency_key=key,
            tenant_digest=self.digest("tenant", tenant_id),
            scope_digest=self.digest("scope", scope),
            event_type=event_type,
            transition=transition,
            reason_code=reason_code,
            epoch=epoch,
            contract_ref=self.digest("contract", contract_ref) if contract_ref else None,
            lease_ref=self.digest("lease", lease_ref) if lease_ref else None,
            job_ref=self.digest("job", job_ref) if job_ref else None,
            retention_ms=retention_ms,
        )

    def append_prepared(
        self,
        event: SemanticMediaAuditEvent,
    ) -> tuple[SemanticMediaAuditEvent, bool]:
        """Persist a prepared event for non-SQL test adapters only."""

        return self._service.append_prepared(event)


def same_idempotent_audit_request(
    first: SemanticMediaAuditEvent,
    second: SemanticMediaAuditEvent,
) -> bool:
    """Compare the command binding, excluding first-write timestamps and ID."""

    return all(
        getattr(first, field) == getattr(second, field)
        for field in (
            "idempotency_digest",
            "tenant_digest",
            "scope_digest",
            "event_type",
            "transition",
            "reason_code",
            "epoch",
            "contract_ref",
            "lease_ref",
            "job_ref",
        )
    )


def _canonical(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest(value: str, reason: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise SemanticMediaAuditError(reason)


def _identifier(value: str, reason: str) -> None:
    if not value or len(value) > 96 or any(not (character.isalnum() or character in "._:-") for character in value):
        raise SemanticMediaAuditError(reason)


__all__ = [
    "InMemorySemanticMediaAuditRepository",
    "MAX_PAGE_SIZE",
    "SemanticMediaAuditError",
    "SemanticMediaAuditEvent",
    "SemanticMediaAuditRepository",
    "SemanticMediaAuditPort",
    "SemanticMediaAuditRecorder",
    "SemanticMediaAuditService",
    "same_idempotent_audit_request",
]
