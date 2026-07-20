"""Thin compatibility adapter from legacy Pair APIs to the shared relay port."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from typing import Any, Callable

from agent.repositories.semantic_relay_repository import (
    SemanticRelayEnvelope,
    SemanticRelayRepository,
    SemanticRelayRepositoryError,
)
from agent.repositories.semantic_relay_shared_store import SharedSemanticRelayRepository
from agent.services.semantic_relay_limits import SemanticRelayLimits


class ShareRelayCompatibilityError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ShareRelayCompatibilityService:
    """Preserve old response shapes without route-owned queues or cursors.

    New semantic paths use :class:`SemanticRelayService` and never expose
    plaintext.  This adapter exists only for backward compatibility with the
    already public Pair View and chat APIs.
    """

    def __init__(
        self,
        repository: SemanticRelayRepository,
        *,
        clock: Callable[[], float] = time.time,
        retention_seconds: int = 300,
    ) -> None:
        if retention_seconds <= 0:
            raise ValueError("share_relay_retention_invalid")
        self._repository = repository
        self._clock = clock
        self._retention_seconds = retention_seconds

    def publish(
        self,
        *,
        tenant_id: str,
        session_id: str,
        epoch: int,
        sender_id: str,
        audience_ids: list[str],
        traffic_class: str,
        item: dict[str, Any],
        item_id_field: str,
        queue_limit: int,
    ) -> int:
        item_id = str(item.get(item_id_field) or "")
        if not item_id or len(item_id.encode()) > 96:
            raise ShareRelayCompatibilityError("relay_item_id_invalid")
        try:
            serialized = json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode()
        except (TypeError, ValueError) as exc:
            raise ShareRelayCompatibilityError("relay_item_invalid") from exc
        now = float(self._clock())
        digest = hashlib.sha256(serialized).hexdigest()
        stored = 0
        for audience_id in sorted(set(audience_ids)):
            if not audience_id or len(audience_id.encode()) > 128:
                raise ShareRelayCompatibilityError("relay_audience_invalid")
            storage_audience = self._storage_audience(audience_id, traffic_class)
            envelope = SemanticRelayEnvelope(
                message_id=f"legacy-{traffic_class[:16]}-{item_id}",
                tenant_id=tenant_id,
                session_id=session_id,
                epoch=epoch,
                sender_id=sender_id,
                audience_id=storage_audience,
                traffic_class=traffic_class,
                payload_bytes=len(serialized),
                payload_digest=digest,
                ciphertext=base64.b64encode(serialized).decode("ascii"),
                expires_at=now + self._retention_seconds,
            )
            try:
                self._repository.append(envelope, now=now)
            except SemanticRelayRepositoryError as exc:
                raise ShareRelayCompatibilityError(exc.reason_code) from exc
            self._trim(
                tenant_id=tenant_id,
                session_id=session_id,
                audience_id=storage_audience,
                queue_limit=queue_limit,
                now=now,
            )
            stored += 1
        return stored

    def publish_secure_envelope(
        self,
        *,
        tenant_id: str,
        session_id: str,
        epoch: int,
        sender_id: str,
        audience_id: str,
        traffic_class: str,
        item_id: str,
        item_id_field: str,
        serialized_envelope: str,
        queue_limit: int,
    ) -> int:
        """Publish a closed opaque item for strict-E2EE compatibility APIs.

        Constructing the item here makes it impossible for strict route code
        to accidentally add plaintext, view hashes or other unprotected
        metadata to the shared relay record.
        """

        if item_id_field not in {"id", "message_id"}:
            raise ShareRelayCompatibilityError("relay_item_id_field_invalid")
        if not isinstance(serialized_envelope, str) or not serialized_envelope:
            raise ShareRelayCompatibilityError("secure_envelope_required")
        if len(serialized_envelope.encode("utf-8")) > 384 * 1024:
            raise ShareRelayCompatibilityError("relay_envelope_too_large")
        return self.publish(
            tenant_id=tenant_id,
            session_id=session_id,
            epoch=epoch,
            sender_id=sender_id,
            audience_ids=[audience_id],
            traffic_class=traffic_class,
            item={item_id_field: item_id, "encrypted_payload": serialized_envelope},
            item_id_field=item_id_field,
            queue_limit=queue_limit,
        )

    def read(
        self,
        *,
        tenant_id: str,
        session_id: str,
        audience_id: str,
        traffic_class: str,
        since_item_id: str,
        item_id_field: str,
        queue_limit: int,
        page_limit: int,
    ) -> tuple[list[dict[str, Any]], str]:
        now = float(self._clock())
        rows = self._repository.read_after(
            tenant_id=tenant_id,
            session_id=session_id,
            audience_id=self._storage_audience(audience_id, traffic_class),
            cursor=0,
            limit=max(queue_limit, page_limit),
            now=now,
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            if row.traffic_class != traffic_class:
                continue
            try:
                decoded = base64.b64decode(row.ciphertext, validate=True)
                value = json.loads(decoded)
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                items.append(value)
        normalized_since = "" if since_item_id == "0" else since_item_id
        if normalized_since:
            for index, item in enumerate(items):
                if str(item.get(item_id_field) or "") == normalized_since:
                    items = items[index + 1 :]
                    break
        page = items[-max(1, page_limit) :]
        cursor = str(page[-1].get(item_id_field) or normalized_since) if page else normalized_since
        return page, cursor

    def clear_session(self, *, tenant_id: str, session_id: str) -> int:
        return self._repository.revoke(tenant_id=tenant_id, session_id=session_id)

    @staticmethod
    def _storage_audience(audience_id: str, traffic_class: str) -> str:
        digest = hashlib.sha256(audience_id.encode("utf-8")).hexdigest()[:32]
        return f"legacy:{traffic_class[:24]}:{digest}"

    def _trim(
        self,
        *,
        tenant_id: str,
        session_id: str,
        audience_id: str,
        queue_limit: int,
        now: float,
    ) -> None:
        rows = self._repository.read_after(
            tenant_id=tenant_id,
            session_id=session_id,
            audience_id=audience_id,
            cursor=0,
            limit=max(1, queue_limit + 1),
            now=now,
        )
        excess = len(rows) - max(1, queue_limit)
        if excess > 0:
            self._repository.acknowledge(
                tenant_id=tenant_id,
                session_id=session_id,
                audience_id=audience_id,
                cursor=rows[excess - 1].cursor,
                now=now,
            )


_SERVICE: ShareRelayCompatibilityService | None = None
_SERVICE_LOCK = threading.Lock()


def get_share_relay_compatibility_service() -> ShareRelayCompatibilityService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                limits = SemanticRelayLimits(max_batch_count=250)
                _SERVICE = ShareRelayCompatibilityService(
                    SharedSemanticRelayRepository(limits),
                    retention_seconds=limits.retention_seconds,
                )
    return _SERVICE


def reset_share_relay_compatibility_service() -> None:
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = None


__all__ = [
    "ShareRelayCompatibilityError",
    "ShareRelayCompatibilityService",
    "get_share_relay_compatibility_service",
    "reset_share_relay_compatibility_service",
]
