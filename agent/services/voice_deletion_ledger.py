from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import portalocker

from agent.config import settings
from agent.services.voice_governance_domain import VoiceGovernanceError, voice_deletion_ledger_signature

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_VERSION = "ananta.voice-deletion-ledger.v1"


@dataclass(frozen=True)
class VoiceDeletionLedgerRecord:
    scope_digest: str
    idempotency_key_digest: str
    deleted_at: float
    record_digest: str


@dataclass(frozen=True)
class VoiceDeletionLedgerClaim:
    record: VoiceDeletionLedgerRecord
    replayed: bool


class _LedgerFileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock: portalocker.Lock | None = None

    def __enter__(self) -> None:
        self._lock = portalocker.Lock(str(self._path), mode="a+", timeout=10)
        self._lock.acquire()
        os.chmod(self._path, 0o600)

    def __exit__(self, _exc_type, _exc, _traceback) -> Literal[False]:
        if self._lock is not None:
            self._lock.release()
        return False


class VoiceDeletionLedger:
    """Segmented, bounded pseudonymous ledger outside the restored database."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_records_per_segment: int | None = None,
        max_total_records: int | None = None,
    ) -> None:
        configured = str(os.getenv("VOICE_DELETION_LEDGER_PATH") or "").strip()
        self._path = path or (
            Path(configured) if configured else Path(settings.data_dir) / "voice-deletion-ledger.v1.jsonl"
        )
        self._max_records_per_segment = self._positive_limit(
            max_records_per_segment,
            env_name="VOICE_DELETION_LEDGER_SEGMENT_RECORDS",
            default=10_000,
        )
        self._max_total_records = self._positive_limit(
            max_total_records,
            env_name="VOICE_DELETION_LEDGER_MAX_RECORDS",
            default=1_000_000,
        )
        if self._max_records_per_segment > self._max_total_records:
            self._max_records_per_segment = self._max_total_records
        self._cached_fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self._cached_records: tuple[VoiceDeletionLedgerRecord, ...] = ()
        self._cached_by_key: dict[str, VoiceDeletionLedgerRecord] = {}

    @property
    def path(self) -> Path:
        return self._path

    def claim(
        self,
        *,
        scope_digest: str,
        idempotency_key_digest: str,
        deleted_at: float | None = None,
    ) -> VoiceDeletionLedgerClaim:
        self._validate_digest(scope_digest, field="scope_digest")
        self._validate_digest(idempotency_key_digest, field="idempotency_key_digest")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock():
            records = self._records_unlocked()
            existing = self._cached_by_key.get(idempotency_key_digest)
            if existing is not None:
                if not hmac.compare_digest(existing.scope_digest, scope_digest):
                    raise VoiceGovernanceError(
                        code="voice_governance.idempotency_conflict",
                        message="idempotency key was already used for a different deletion scope",
                        status_code=409,
                    )
                return VoiceDeletionLedgerClaim(record=existing, replayed=True)
            if len(records) >= self._max_total_records:
                raise VoiceGovernanceError(
                    code="voice_deletion_ledger.capacity_exhausted",
                    message="voice deletion ledger reached its configured record limit",
                    status_code=503,
                )
            self._rotate_active_segment_unlocked()
            timestamp = float(deleted_at if deleted_at is not None else time.time())
            if timestamp <= 0:
                raise ValueError("voice deletion timestamp must be positive")
            previous_digest = records[-1].record_digest if records else "0" * 64
            payload = {
                "schema_version": _SCHEMA_VERSION,
                "scope_digest": scope_digest,
                "idempotency_key_digest": idempotency_key_digest,
                "deleted_at": timestamp,
                "previous_digest": previous_digest,
            }
            signature = voice_deletion_ledger_signature(self._canonical(payload))
            persisted = {**payload, "record_hmac": signature}
            encoded = self._canonical(persisted)
            with self._path.open("a", encoding="utf-8") as handle:
                os.chmod(self._path, 0o600)
                handle.write(encoded.decode("utf-8") + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            record = VoiceDeletionLedgerRecord(
                scope_digest=scope_digest,
                idempotency_key_digest=idempotency_key_digest,
                deleted_at=timestamp,
                record_digest=hashlib.sha256(encoded).hexdigest(),
            )
            self._cached_records = (*records, record)
            self._cached_by_key[idempotency_key_digest] = record
            self._cached_fingerprint = self._fingerprint_unlocked()
            return VoiceDeletionLedgerClaim(record=record, replayed=False)

    def read_all(self) -> tuple[VoiceDeletionLedgerRecord, ...]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock():
            return self._records_unlocked()

    @property
    def segment_paths(self) -> tuple[Path, ...]:
        return tuple(self._segment_paths_unlocked())

    def _records_unlocked(self) -> tuple[VoiceDeletionLedgerRecord, ...]:
        fingerprint = self._fingerprint_unlocked()
        if fingerprint == self._cached_fingerprint:
            return self._cached_records
        records = tuple(self._load_records_unlocked())
        by_key: dict[str, VoiceDeletionLedgerRecord] = {}
        for record in records:
            existing = by_key.get(record.idempotency_key_digest)
            if existing is not None and not hmac.compare_digest(existing.scope_digest, record.scope_digest):
                raise VoiceGovernanceError(
                    code="voice_deletion_ledger.idempotency_conflict",
                    message="voice deletion ledger contains a conflicting idempotency digest",
                    status_code=500,
                )
            by_key[record.idempotency_key_digest] = record
        self._cached_records = records
        self._cached_by_key = by_key
        self._cached_fingerprint = fingerprint
        return records

    def _load_records_unlocked(self) -> list[VoiceDeletionLedgerRecord]:
        records: list[VoiceDeletionLedgerRecord] = []
        previous_digest = "0" * 64
        for path in (*self._segment_paths_unlocked(), self._path):
            if not path.exists():
                continue
            parsed, previous_digest = self._parse_lines(
                path.read_text(encoding="utf-8").splitlines(),
                previous_digest=previous_digest,
            )
            records.extend(parsed)
        return records

    def _parse_lines(
        self,
        lines: list[str],
        *,
        previous_digest: str,
    ) -> tuple[list[VoiceDeletionLedgerRecord], str]:
        records: list[VoiceDeletionLedgerRecord] = []
        for raw_line in lines:
            if not raw_line.strip():
                continue
            try:
                payload = json.loads(raw_line)
            except ValueError as exc:
                raise VoiceGovernanceError(
                    code="voice_deletion_ledger.invalid_record",
                    message="voice deletion ledger contains invalid JSON",
                    status_code=500,
                ) from exc
            if not isinstance(payload, dict) or payload.get("schema_version") != _SCHEMA_VERSION:
                raise VoiceGovernanceError(
                    code="voice_deletion_ledger.invalid_record",
                    message="voice deletion ledger record is invalid",
                    status_code=500,
                )
            signature = str(payload.pop("record_hmac", ""))
            expected_signature = voice_deletion_ledger_signature(self._canonical(payload))
            if not hmac.compare_digest(signature, expected_signature):
                raise VoiceGovernanceError(
                    code="voice_deletion_ledger.integrity_failed",
                    message="voice deletion ledger integrity validation failed",
                    status_code=500,
                )
            if payload.get("previous_digest") != previous_digest:
                raise VoiceGovernanceError(
                    code="voice_deletion_ledger.chain_failed",
                    message="voice deletion ledger chain validation failed",
                    status_code=500,
                )
            scope_digest = str(payload.get("scope_digest") or "")
            key_digest = str(payload.get("idempotency_key_digest") or "")
            deleted_at = float(payload.get("deleted_at") or 0)
            if deleted_at <= 0:
                raise VoiceGovernanceError(
                    code="voice_deletion_ledger.invalid_record",
                    message="voice deletion ledger timestamp is invalid",
                    status_code=500,
                )
            self._validate_digest(scope_digest, field="scope_digest")
            self._validate_digest(key_digest, field="idempotency_key_digest")
            encoded = self._canonical({**payload, "record_hmac": signature})
            record_digest = hashlib.sha256(encoded).hexdigest()
            records.append(
                VoiceDeletionLedgerRecord(
                    scope_digest=scope_digest,
                    idempotency_key_digest=key_digest,
                    deleted_at=deleted_at,
                    record_digest=record_digest,
                )
            )
            previous_digest = record_digest
        return records, previous_digest

    def _rotate_active_segment_unlocked(self) -> None:
        if not self._path.exists():
            return
        active_records = sum(1 for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip())
        if active_records < self._max_records_per_segment:
            return
        existing = self._segment_paths_unlocked()
        sequence = len(existing) + 1
        rotated = self._path.with_name(f"{self._path.name}.segment-{sequence:08d}")
        if rotated.exists():
            raise VoiceGovernanceError(
                code="voice_deletion_ledger.segment_conflict",
                message="voice deletion ledger segment sequence is invalid",
                status_code=500,
            )
        os.replace(self._path, rotated)
        os.chmod(rotated, 0o600)

    def _segment_paths_unlocked(self) -> list[Path]:
        return sorted(self._path.parent.glob(f"{self._path.name}.segment-[0-9]*"))

    def _fingerprint_unlocked(self) -> tuple[tuple[str, int, int], ...]:
        values = []
        for path in (*self._segment_paths_unlocked(), self._path):
            if path.exists():
                stat = path.stat()
                values.append((path.name, stat.st_size, stat.st_mtime_ns))
        return tuple(values)

    def _lock(self) -> _LedgerFileLock:
        lock_path = self._path.with_name(f"{self._path.name}.lock")
        return _LedgerFileLock(lock_path)

    @staticmethod
    def _positive_limit(value: int | None, *, env_name: str, default: int) -> int:
        resolved = int(value if value is not None else os.getenv(env_name, str(default)))
        if resolved <= 0:
            raise ValueError(f"{env_name} must be positive")
        return resolved

    @staticmethod
    def _canonical(value: dict) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    @staticmethod
    def _validate_digest(value: str, *, field: str) -> None:
        if _DIGEST_RE.fullmatch(value) is None:
            raise VoiceGovernanceError(
                code="voice_deletion_ledger.invalid_digest",
                message=f"voice deletion ledger {field} is invalid",
                status_code=500,
            )
