"""Instance-scoped secret-store adapter for SFU member digest rotation.

This adapter provides deterministic lifecycle behavior and no module-level
state.  A production KMS adapter can implement the same read port while keeping
the provider unchanged.  ``destroy`` tombstones the record and drops the Python
reference; a KMS implementation must additionally perform backend destruction.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import RLock
from typing import Iterable

from agent.services.sfu_member_digest_key_provider import (
    DigestKeyState,
    SfuMemberDigestKeyRecord,
)


class SfuMemberDigestSecretStoreAdapter:
    """Thread-safe lifecycle adapter with explicit, monotonic transitions."""

    def __init__(
        self, records: Iterable[SfuMemberDigestKeyRecord] = ()
    ) -> None:
        self._lock = RLock()
        self._records: dict[str, SfuMemberDigestKeyRecord] = {}
        for record in records:
            if record.key_id in self._records:
                raise ValueError(f"duplicate digest key id: {record.key_id}")
            self._records[record.key_id] = record

    def stage_generation(
        self,
        *,
        key_id: str,
        generation: int,
        algorithm: str,
        scope: str,
        secret: bytes,
    ) -> None:
        """Stage dedicated non-media secret bytes in the unusable generation state."""

        if not isinstance(secret, bytes):
            raise TypeError("digest secret must be bytes")
        with self._lock:
            if key_id in self._records:
                raise ValueError(f"digest key already exists: {key_id}")
            self._records[key_id] = SfuMemberDigestKeyRecord(
                key_id=key_id,
                generation=generation,
                algorithm=algorithm,
                scope=scope,
                state=DigestKeyState.GENERATION,
                secret=bytes(secret),
            )

    def activate(
        self, *, key_id: str, valid_from: datetime, valid_until: datetime
    ) -> None:
        """Move a staged generation into a bounded signing window."""

        self._bounded_transition(
            key_id=key_id,
            expected=DigestKeyState.GENERATION,
            target=DigestKeyState.ACTIVE,
            valid_from=valid_from,
            valid_until=valid_until,
        )

    def begin_dual_read(
        self, *, key_id: str, valid_from: datetime, valid_until: datetime
    ) -> None:
        """Make the former active generation verify-only for a bounded overlap."""

        self._bounded_transition(
            key_id=key_id,
            expected=DigestKeyState.ACTIVE,
            target=DigestKeyState.DUAL_READ,
            valid_from=valid_from,
            valid_until=valid_until,
        )

    def retire(self, *, key_id: str) -> None:
        """Make a dual-read generation unusable while retaining rotation metadata."""

        with self._lock:
            record = self._require_state(key_id, DigestKeyState.DUAL_READ)
            self._records[key_id] = replace(
                record,
                state=DigestKeyState.RETIRED,
                valid_from=None,
                valid_until=None,
            )

    def destroy(self, *, key_id: str) -> None:
        """Drop secret material and retain a destroyed tombstone."""

        with self._lock:
            record = self._require_state(key_id, DigestKeyState.RETIRED)
            self._records[key_id] = replace(
                record,
                state=DigestKeyState.DESTROYED,
                secret=None,
                valid_from=None,
                valid_until=None,
            )

    def get(self, key_id: str) -> SfuMemberDigestKeyRecord | None:
        with self._lock:
            return self._records.get(key_id)

    def list_for_scope(self, scope: str) -> tuple[SfuMemberDigestKeyRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (record for record in self._records.values() if record.scope == scope),
                    key=lambda record: (record.generation, record.key_id),
                )
            )

    def _bounded_transition(
        self,
        *,
        key_id: str,
        expected: DigestKeyState,
        target: DigestKeyState,
        valid_from: datetime,
        valid_until: datetime,
    ) -> None:
        if not isinstance(valid_from, datetime) or not isinstance(valid_until, datetime):
            raise TypeError("validity bounds must be datetimes")
        if valid_from.tzinfo is None or valid_until.tzinfo is None:
            raise ValueError("validity bounds must be timezone-aware")
        if valid_from.utcoffset() is None or valid_until.utcoffset() is None:
            raise ValueError("validity bounds must be timezone-aware")
        if valid_from >= valid_until:
            raise ValueError("validity window must be positive")
        with self._lock:
            record = self._require_state(key_id, expected)
            self._records[key_id] = replace(
                record,
                state=target,
                valid_from=valid_from,
                valid_until=valid_until,
            )

    def _require_state(
        self, key_id: str, expected: DigestKeyState
    ) -> SfuMemberDigestKeyRecord:
        try:
            record = self._records[key_id]
        except KeyError as exc:
            raise KeyError(f"unknown digest key: {key_id}") from exc
        if record.state != expected:
            raise ValueError(
                f"digest key {key_id} is {record.state!s}, expected {expected.value}"
            )
        return record
