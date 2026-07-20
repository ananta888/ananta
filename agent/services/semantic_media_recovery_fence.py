"""Generic Hub-owned attempt/epoch/consent fence for restart-safe result admission."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, replace


class RecoveryFenceError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RecoveryAttempt:
    scope_digest: str
    epoch: int
    consent_version: int
    attempt: int
    state: str
    result_digest: str | None = None


class SemanticMediaRecoveryFence:
    """Admits a result once; never schedules work or talks to another worker."""

    def __init__(self, *, maximum_attempts: int = 3) -> None:
        if not 1 <= maximum_attempts <= 10:
            raise RecoveryFenceError("recovery_attempt_policy_invalid")
        self._maximum_attempts = maximum_attempts
        self._lock = threading.RLock()
        self._attempts: dict[str, RecoveryAttempt] = {}
        self._resources: dict[str, set[str]] = {}

    def begin(self, *, scope_digest: str, epoch: int, consent_version: int) -> RecoveryAttempt:
        _digest(scope_digest, "recovery_scope_invalid")
        if epoch < 1 or consent_version < 1:
            raise RecoveryFenceError("recovery_authority_invalid")
        with self._lock:
            previous = self._attempts.get(scope_digest)
            attempt = 1 if previous is None else previous.attempt + 1
            if attempt > self._maximum_attempts:
                raise RecoveryFenceError("recovery_attempts_exhausted")
            row = RecoveryAttempt(scope_digest, epoch, consent_version, attempt, "active")
            self._attempts[scope_digest] = row
            return row

    def register_resource(self, attempt: RecoveryAttempt, *, kind: str, opaque_id: str) -> None:
        if kind not in {"timer", "track", "temporary", "reservation"}:
            raise RecoveryFenceError("recovery_resource_kind_invalid")
        _opaque(opaque_id)
        with self._lock:
            self._assert_current(attempt)
            self._resources.setdefault(_attempt_key(attempt), set()).add(f"{kind}:{opaque_id}")

    def commit(self, attempt: RecoveryAttempt, *, result_digest: str) -> RecoveryAttempt:
        _digest(result_digest, "recovery_result_digest_invalid")
        with self._lock:
            current = self._assert_current(attempt)
            if current.state == "committed":
                if current.result_digest != result_digest:
                    raise RecoveryFenceError("recovery_result_conflict")
                return current
            if current.state != "active":
                raise RecoveryFenceError("recovery_attempt_fenced")
            committed = replace(current, state="committed", result_digest=result_digest)
            self._attempts[current.scope_digest] = committed
            return committed

    def fence(
        self,
        *,
        scope_digest: str,
        minimum_epoch: int | None = None,
        minimum_consent_version: int | None = None,
    ) -> RecoveryAttempt | None:
        with self._lock:
            current = self._attempts.get(scope_digest)
            if current is None:
                return None
            if minimum_epoch is not None and current.epoch >= minimum_epoch:
                return current
            if minimum_consent_version is not None and current.consent_version >= minimum_consent_version:
                return current
            fenced = replace(current, state="fenced")
            self._attempts[scope_digest] = fenced
            self._resources.pop(_attempt_key(current), None)
            return fenced

    def cleanup(self, attempt: RecoveryAttempt) -> int:
        with self._lock:
            return len(self._resources.pop(_attempt_key(attempt), set()))

    def resource_count(self) -> int:
        with self._lock:
            return sum(map(len, self._resources.values()))

    def _assert_current(self, attempt: RecoveryAttempt) -> RecoveryAttempt:
        current = self._attempts.get(attempt.scope_digest)
        if current is None or current.attempt != attempt.attempt:
            raise RecoveryFenceError("recovery_attempt_stale")
        if current.epoch != attempt.epoch:
            raise RecoveryFenceError("recovery_epoch_stale")
        if current.consent_version != attempt.consent_version:
            raise RecoveryFenceError("recovery_consent_stale")
        return current


def _attempt_key(attempt: RecoveryAttempt) -> str:
    return hashlib.sha256(
        f"{attempt.scope_digest}:{attempt.epoch}:{attempt.consent_version}:{attempt.attempt}".encode()
    ).hexdigest()


def _digest(value: str, reason: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RecoveryFenceError(reason)


def _opaque(value: str) -> None:
    if not value or len(value) > 96 or value.startswith(("/", "file:", "~")) or "\\" in value:
        raise RecoveryFenceError("recovery_resource_id_invalid")


__all__ = ["RecoveryAttempt", "RecoveryFenceError", "SemanticMediaRecoveryFence"]
