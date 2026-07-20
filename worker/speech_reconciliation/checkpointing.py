"""Encrypted, exact-binding checkpoints for one reconciliation attempt."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ananta_contracts.speech_reconciliation import canonical_json
from worker.speech_reconciliation.audio_staging import (
    SpeechArtifactKeyResolver,
    SpeechAudioStagingError,
    derive_artifact_key,
)
from worker.speech_reconciliation.contracts import (
    CONTRACT_VERSION,
    SpeechReconciliationCheckpoint,
    SpeechReconciliationWorkerTask,
)


class SpeechCheckpointError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


class SpeechCheckpointCipher(Protocol):
    def seal(self, *, artifact_ref: str, key_epoch: int, aad: bytes, plaintext: bytes) -> bytes: ...

    def open(self, *, artifact_ref: str, key_epoch: int, aad: bytes, ciphertext: bytes) -> bytes: ...


class AesGcmSpeechCheckpointCipher:
    def __init__(self, keys: SpeechArtifactKeyResolver, *, nonce_factory=os.urandom) -> None:
        self._keys = keys
        self._nonce_factory = nonce_factory

    def seal(self, *, artifact_ref: str, key_epoch: int, aad: bytes, plaintext: bytes) -> bytes:
        nonce = self._nonce_factory(12)
        if len(nonce) != 12:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_nonce_invalid")
        key = self._key(artifact_ref=artifact_ref, key_epoch=key_epoch)
        return nonce + AESGCM(key).encrypt(nonce, bytes(plaintext), aad)

    def open(self, *, artifact_ref: str, key_epoch: int, aad: bytes, ciphertext: bytes) -> bytes:
        if len(ciphertext) < 28:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_truncated")
        key = self._key(artifact_ref=artifact_ref, key_epoch=key_epoch)
        try:
            return AESGCM(key).decrypt(ciphertext[:12], ciphertext[12:], aad)
        except InvalidTag as exc:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_authentication_failed") from exc

    def _key(self, *, artifact_ref: str, key_epoch: int) -> bytes:
        try:
            root = self._keys.resolve(key_epoch=key_epoch, artifact_ref=artifact_ref)
            return derive_artifact_key(root, key_epoch=key_epoch, artifact_ref=artifact_ref)
        except SpeechAudioStagingError as exc:
            raise SpeechCheckpointError(exc.reason_code, retryable=exc.retryable) from exc


@dataclass(frozen=True)
class LoadedSpeechCheckpoint:
    checkpoint: SpeechReconciliationCheckpoint
    state: bytes


class SpeechReconciliationCheckpointStore:
    """Content-addressed local spool; the Hub remains artifact owner/publisher."""

    def __init__(
        self,
        root: Path,
        *,
        cipher: SpeechCheckpointCipher,
        hard_max_checkpoint_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if hard_max_checkpoint_bytes < 1:
            raise ValueError("checkpoint byte limit must be positive")
        self._root = _prepare_root(root)
        self._cipher = cipher
        self._hard_max = hard_max_checkpoint_bytes

    def save(
        self,
        task: SpeechReconciliationWorkerTask,
        *,
        checkpoint_sequence: int,
        stage: str,
        state: bytes,
    ) -> SpeechReconciliationCheckpoint:
        if checkpoint_sequence < 1 or not stage:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_metadata_invalid")
        payload = bytes(state)
        maximum = min(self._hard_max, task.budget_ledger.remaining.checkpoint_bytes)
        if not payload or len(payload) > maximum:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_budget_exceeded")
        reference = (
            "artifact://speech-reconciliation-checkpoints/"
            f"{task.job.job_id}/{task.job.attempt_id}/{checkpoint_sequence}"
        )
        state_digest = hashlib.sha256(payload).hexdigest()
        base = _checkpoint_mapping(
            task,
            checkpoint_digest="0" * 64,
            checkpoint_ref=reference,
            checkpoint_sequence=checkpoint_sequence,
            stage=stage,
            state_digest=state_digest,
        )
        aad = canonical_json(_checkpoint_aad(base))
        sealed = self._cipher.seal(
            artifact_ref=reference,
            key_epoch=task.job.key_epoch,
            aad=aad,
            plaintext=payload,
        )
        if len(sealed) > maximum:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_budget_exceeded")
        checkpoint_digest = hashlib.sha256(sealed).hexdigest()
        checkpoint = SpeechReconciliationCheckpoint.from_mapping({**base, "checkpoint_digest": checkpoint_digest})
        assert_checkpoint_matches_task(task, checkpoint)
        self._write_once(checkpoint_digest, sealed, maximum=maximum)
        return checkpoint

    def stage_resume(
        self,
        task: SpeechReconciliationWorkerTask,
        checkpoint: SpeechReconciliationCheckpoint,
        ciphertext: bytes,
    ) -> None:
        assert_checkpoint_matches_task(task, checkpoint)
        payload = bytes(ciphertext)
        maximum = min(self._hard_max, task.budget_ledger.remaining.checkpoint_bytes)
        if not payload or len(payload) > maximum:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_budget_exceeded")
        if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), checkpoint.checkpoint_digest):
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_digest_mismatch")
        self._write_once(checkpoint.checkpoint_digest, payload, maximum=maximum)

    def resume(
        self,
        task: SpeechReconciliationWorkerTask,
        checkpoint: SpeechReconciliationCheckpoint,
        *,
        expected_stage: str,
    ) -> LoadedSpeechCheckpoint:
        assert_checkpoint_matches_task(task, checkpoint)
        if checkpoint.stage != expected_stage:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_stage_mismatch")
        maximum = min(self._hard_max, task.budget_ledger.remaining.checkpoint_bytes)
        sealed = self._read(checkpoint.checkpoint_digest, maximum=maximum)
        if not hmac.compare_digest(hashlib.sha256(sealed).hexdigest(), checkpoint.checkpoint_digest):
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_digest_mismatch")
        mapping = checkpoint.to_dict()
        state = self._cipher.open(
            artifact_ref=checkpoint.checkpoint_ref,
            key_epoch=checkpoint.key_epoch,
            aad=canonical_json(_checkpoint_aad(mapping)),
            ciphertext=sealed,
        )
        if not hmac.compare_digest(hashlib.sha256(state).hexdigest(), checkpoint.state_digest):
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_state_mismatch")
        return LoadedSpeechCheckpoint(checkpoint=checkpoint, state=state)

    def read_ciphertext(self, checkpoint: SpeechReconciliationCheckpoint) -> bytes:
        return self._read(checkpoint.checkpoint_digest, maximum=self._hard_max)

    def delete(self, checkpoint_digest: str) -> bool:
        path = self._path(checkpoint_digest)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def _write_once(self, digest: str, payload: bytes, *, maximum: int) -> None:
        path = self._path(digest)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = self._read(digest, maximum=maximum)
            if not hmac.compare_digest(existing, payload):
                raise SpeechCheckpointError("speech_reconciliation_checkpoint_write_conflict")
            return
        except OSError as exc:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_write_failed", retryable=True) from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            path.unlink(missing_ok=True)
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_write_failed", retryable=True) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _read(self, digest: str, *, maximum: int) -> bytes:
        path = self._path(digest)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError as exc:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_missing", retryable=True) from exc
        except OSError as exc:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_boundary_violation") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size < 28 or metadata.st_size > maximum:
                raise SpeechCheckpointError("speech_reconciliation_checkpoint_size_invalid")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                payload = handle.read(maximum + 1)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(payload) > maximum:
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_size_invalid")
        return payload

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_digest_invalid")
        return self._root / f"{digest}.checkpoint"


def assert_checkpoint_matches_task(
    task: SpeechReconciliationWorkerTask,
    checkpoint: SpeechReconciliationCheckpoint,
) -> None:
    job = task.job
    expected = {
        "contract_version": CONTRACT_VERSION,
        "job_id": job.job_id,
        "attempt_id": job.attempt_id,
        "fencing_token_digest": job.fencing_token_digest,
        "fencing_epoch": job.fencing_epoch,
        "consent_id": job.consent_id,
        "consent_version": job.consent_version,
        "revocation_epoch": job.revocation_epoch,
        "input_manifest_digest": job.input_manifest_digest,
        "policy_digest": job.policy_digest,
        "ledger_sequence": job.ledger_sequence,
        "key_epoch": job.key_epoch,
    }
    if any(getattr(checkpoint, field) != value for field, value in expected.items()):
        raise SpeechCheckpointError("speech_reconciliation_checkpoint_binding_mismatch")


def _checkpoint_mapping(
    task: SpeechReconciliationWorkerTask,
    *,
    checkpoint_digest: str,
    checkpoint_ref: str,
    checkpoint_sequence: int,
    stage: str,
    state_digest: str,
) -> dict[str, object]:
    job = task.job
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": job.job_id,
        "attempt_id": job.attempt_id,
        "fencing_token_digest": job.fencing_token_digest,
        "fencing_epoch": job.fencing_epoch,
        "consent_id": job.consent_id,
        "consent_version": job.consent_version,
        "revocation_epoch": job.revocation_epoch,
        "input_manifest_digest": job.input_manifest_digest,
        "policy_digest": job.policy_digest,
        "ledger_sequence": job.ledger_sequence,
        "key_epoch": job.key_epoch,
        "checkpoint_digest": checkpoint_digest,
        "checkpoint_ref": checkpoint_ref,
        "checkpoint_sequence": checkpoint_sequence,
        "stage": stage,
        "state_digest": state_digest,
    }


def _checkpoint_aad(mapping: Mapping[str, object]) -> dict[str, object]:
    # The ciphertext digest is necessarily outside its own AEAD AAD. Every
    # other resume binding, including the plaintext state digest, is covered.
    return {key: value for key, value in mapping.items() if key != "checkpoint_digest"}


def _prepare_root(path: Path) -> Path:
    root = Path(path).absolute()
    try:
        parent = root.parent
        parent_metadata = parent.lstat()
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or stat.S_ISLNK(parent_metadata.st_mode)
            or parent.resolve(strict=True) != parent
        ):
            raise SpeechCheckpointError("speech_reconciliation_checkpoint_boundary_violation")
        if root.exists() or root.is_symlink():
            metadata = root.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise SpeechCheckpointError("speech_reconciliation_checkpoint_boundary_violation")
        else:
            root.mkdir(mode=0o700)
        root.chmod(0o700)
    except SpeechCheckpointError:
        raise
    except OSError as exc:
        raise SpeechCheckpointError("speech_reconciliation_checkpoint_root_unavailable", retryable=True) from exc
    return root


__all__ = [
    "AesGcmSpeechCheckpointCipher",
    "LoadedSpeechCheckpoint",
    "SpeechCheckpointCipher",
    "SpeechCheckpointError",
    "SpeechReconciliationCheckpointStore",
    "assert_checkpoint_matches_task",
]
