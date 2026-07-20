"""Fail-closed encrypted-audio staging for the reconciliation worker."""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping, Protocol

from ananta_contracts.speech_reconciliation_crypto import (
    SpeechReconciliationCryptoError,
    SpeechReconciliationEpochKeyring,
    derive_speech_reconciliation_artifact_key,
    open_speech_reconciliation_audio,
)
from voice_runtime.preprocessing.audio_decode import (
    AudioDecodeError,
    AudioDecodeLimits,
    AudioDecoder,
    DecodedPcmAudio,
    SafeAudioDecoder,
)
from voice_runtime.preprocessing.temp_workspace import AudioWorkspace
from worker.speech_reconciliation.contracts import SpeechReconciliationWorkerTask

_WORKSPACE_PREFIX = "speech-reconciliation-"


class SpeechAudioStagingError(RuntimeError):
    """Content-free failure returned across the worker control boundary."""

    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


class SpeechArtifactDecryptor(Protocol):
    def decrypt(self, task: SpeechReconciliationWorkerTask, ciphertext: bytes) -> bytes: ...


class SpeechArtifactKeyResolver(Protocol):
    def resolve(self, *, key_epoch: int, artifact_ref: str) -> bytes: ...


class SpeechStageAuthority(Protocol):
    def verify(self, task: SpeechReconciliationWorkerTask, *, phase: str) -> tuple[bool, str | None]: ...


class CancellationCheck(Protocol):
    def __call__(self) -> None: ...


@dataclass(frozen=True)
class SpeechAudioStagingLimits:
    max_ciphertext_bytes: int = 128 * 1024 * 1024
    max_plaintext_bytes: int = 96 * 1024 * 1024
    max_decoded_pcm_bytes: int = 256 * 1024 * 1024
    max_workspace_bytes: int = 512 * 1024 * 1024
    max_duration_ms: int = 8 * 60 * 60 * 1000
    decoder_timeout_seconds: int = 120

    def __post_init__(self) -> None:
        values = (
            self.max_ciphertext_bytes,
            self.max_plaintext_bytes,
            self.max_decoded_pcm_bytes,
            self.max_workspace_bytes,
            self.max_duration_ms,
            self.decoder_timeout_seconds,
        )
        if any(value <= 0 for value in values):
            raise ValueError("speech reconciliation staging limits must be positive")
        if self.max_workspace_bytes < self.max_plaintext_bytes:
            raise ValueError("speech reconciliation workspace cannot hold admitted plaintext")


@dataclass(frozen=True)
class StagedSpeechAudio:
    decoded: DecodedPcmAudio
    encoded_path: Path
    wav_path: Path
    source_audio_digest: str
    workspace_root: Path


class EpochKeyring:
    """Read-only, bounded local key resolver; it never reaches Hub or a peer."""

    def __init__(self, keys: Mapping[int, bytes]) -> None:
        try:
            self._delegate = SpeechReconciliationEpochKeyring(keys)
        except SpeechReconciliationCryptoError as exc:
            raise _staging_crypto_error(exc) from exc

    @classmethod
    def from_file(cls, path: Path) -> "EpochKeyring":
        try:
            delegate = SpeechReconciliationEpochKeyring.from_file(path)
        except SpeechReconciliationCryptoError as exc:
            raise _staging_crypto_error(exc) from exc
        instance = cls.__new__(cls)
        instance._delegate = delegate
        return instance

    def resolve(self, *, key_epoch: int, artifact_ref: str) -> bytes:
        try:
            return self._delegate.resolve(key_epoch=key_epoch, artifact_ref=artifact_ref)
        except SpeechReconciliationCryptoError as exc:
            raise _staging_crypto_error(exc) from exc


class AesGcmSpeechArtifactDecryptor:
    """Decrypt a nonce-prefixed AES-GCM artifact with job-bound AAD."""

    NONCE_BYTES = 12
    TAG_BYTES = 16

    def __init__(self, keys: SpeechArtifactKeyResolver) -> None:
        self._keys = keys

    def decrypt(self, task: SpeechReconciliationWorkerTask, ciphertext: bytes) -> bytes:
        root_key = self._keys.resolve(
            key_epoch=task.job.key_epoch,
            artifact_ref=task.audio_artifact.artifact_ref,
        )
        if len(root_key) != 32:
            raise SpeechAudioStagingError("speech_reconciliation_artifact_key_invalid")
        try:
            return open_speech_reconciliation_audio(
                root_key=root_key,
                artifact=task.audio_artifact,
                job=task.job,
                ciphertext=ciphertext,
            )
        except SpeechReconciliationCryptoError as exc:
            raise _staging_crypto_error(exc) from exc


def derive_artifact_key(root_key: bytes, *, key_epoch: int, artifact_ref: str) -> bytes:
    """Derive one artifact-scoped key from a deployment-local epoch secret."""

    try:
        return derive_speech_reconciliation_artifact_key(
            root_key,
            key_epoch=key_epoch,
            artifact_ref=artifact_ref,
        )
    except SpeechReconciliationCryptoError as exc:
        raise _staging_crypto_error(exc) from exc


def _staging_crypto_error(error: SpeechReconciliationCryptoError) -> SpeechAudioStagingError:
    return SpeechAudioStagingError(error.reason_code, retryable=error.retryable)


class SpeechAudioStager:
    """Validate, decrypt, decode and remove one bounded task artifact.

    Every temporary directory lives under a dedicated tmpfs root.  A restart
    sweep removes remnants from SIGKILL/container crashes before readiness.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        decryptor: SpeechArtifactDecryptor,
        authority: SpeechStageAuthority,
        limits: SpeechAudioStagingLimits | None = None,
        decoder_factory: Callable[[AudioDecodeLimits], AudioDecoder] | None = None,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        self._root = _prepare_root(workspace_root)
        self._decryptor = decryptor
        self._authority = authority
        self._limits = limits or SpeechAudioStagingLimits()
        self._decoder_factory = decoder_factory or (lambda value: SafeAudioDecoder(limits=value))
        self._clock_ms = clock_ms
        self.cleanup_stale_workspaces()

    @contextmanager
    def stage(
        self,
        task: SpeechReconciliationWorkerTask,
        ciphertext: bytes,
        *,
        cancellation_check: CancellationCheck | None = None,
    ) -> Iterator[StagedSpeechAudio]:
        payload = bytes(ciphertext)
        self._validate_before_decrypt(task, payload, cancellation_check)
        workspace_path = self._new_workspace(task)
        workspace = AudioWorkspace(workspace_path)
        failure: BaseException | None = None
        try:
            plaintext = self._decryptor.decrypt(task, payload)
            self._check_cancel(cancellation_check)
            self._validate_plaintext(task, plaintext)
            encoded_path = workspace.write_bytes(
                "source.audio",
                plaintext,
                max_bytes=min(task.audio_artifact.plaintext_bytes, self._limits.max_plaintext_bytes),
            )
            decoder_limits = self._decode_limits(task)
            decoder = self._decoder_factory(decoder_limits)
            try:
                decoded = decoder.decode(filename=task.audio_artifact.filename, payload=plaintext)
            except AudioDecodeError as exc:
                raise SpeechAudioStagingError(_safe_decode_reason(exc.code)) from exc
            except TimeoutError as exc:
                raise SpeechAudioStagingError("speech_reconciliation_decoder_timeout", retryable=True) from exc
            except Exception as exc:
                raise SpeechAudioStagingError("speech_reconciliation_decoder_failed") from exc
            self._check_cancel(cancellation_check)
            if (
                len(decoded.pcm_s16le) > task.audio_artifact.decoded_pcm_bytes
                or decoded.duration_ms > task.audio_artifact.duration_ms
            ):
                raise SpeechAudioStagingError("speech_reconciliation_decoded_audio_binding_mismatch")
            wav_payload = decoded.to_wav_bytes()
            used = len(payload) + len(plaintext) + len(wav_payload)
            admitted = min(self._limits.max_workspace_bytes, task.budget_ledger.remaining.disk_bytes)
            if used > admitted:
                raise SpeechAudioStagingError("speech_reconciliation_stage_disk_budget_exceeded")
            wav_path = workspace.write_bytes(
                "decoded.wav",
                wav_payload,
                max_bytes=task.audio_artifact.decoded_pcm_bytes + 4096,
            )
            self._verify_authority(task, "after_audio_decode")
            yield StagedSpeechAudio(
                decoded=decoded,
                encoded_path=encoded_path,
                wav_path=wav_path,
                source_audio_digest=task.audio_artifact.content_digest,
                workspace_root=workspace_path,
            )
        except BaseException as exc:
            failure = exc
            raise
        finally:
            try:
                _remove_workspace(workspace_path)
            except OSError as cleanup_error:
                if failure is None:
                    raise SpeechAudioStagingError("speech_reconciliation_workspace_cleanup_failed") from cleanup_error

    def cleanup_stale_workspaces(self) -> int:
        removed = 0
        for entry in os.scandir(self._root):
            if not entry.name.startswith(_WORKSPACE_PREFIX):
                continue
            _remove_workspace(Path(entry.path))
            removed += 1
        return removed

    def _validate_before_decrypt(
        self,
        task: SpeechReconciliationWorkerTask,
        payload: bytes,
        cancellation_check: CancellationCheck | None,
    ) -> None:
        self._check_cancel(cancellation_check)
        if self._clock_ms() >= task.job.deadline_at_ms:
            raise SpeechAudioStagingError("speech_reconciliation_deadline_expired")
        if task.job.stage not in {"staging", "audio_staging", "slow_asr", "alignment", "resolution"}:
            raise SpeechAudioStagingError("speech_reconciliation_stage_invalid")
        if task.job.consent_version < 1 or task.job.revocation_epoch < 0:
            raise SpeechAudioStagingError("speech_reconciliation_consent_binding_invalid")
        if task.audio_artifact.key_epoch != task.job.key_epoch:
            raise SpeechAudioStagingError("speech_reconciliation_key_epoch_mismatch")
        if len(payload) != task.audio_artifact.ciphertext_bytes:
            raise SpeechAudioStagingError("speech_reconciliation_artifact_size_mismatch")
        if len(payload) > self._limits.max_ciphertext_bytes:
            raise SpeechAudioStagingError("speech_reconciliation_artifact_size_limit")
        actual_digest = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual_digest, task.audio_artifact.transport_digest):
            raise SpeechAudioStagingError("speech_reconciliation_artifact_transport_tamper")
        required_disk = len(payload) + task.audio_artifact.plaintext_bytes + task.audio_artifact.decoded_pcm_bytes
        if required_disk > min(self._limits.max_workspace_bytes, task.budget_ledger.remaining.disk_bytes):
            raise SpeechAudioStagingError("speech_reconciliation_stage_disk_budget_exceeded")
        self._verify_authority(task, "before_audio_decrypt")

    def _validate_plaintext(self, task: SpeechReconciliationWorkerTask, plaintext: bytes) -> None:
        if len(plaintext) != task.audio_artifact.plaintext_bytes:
            raise SpeechAudioStagingError("speech_reconciliation_artifact_plaintext_size_mismatch")
        if len(plaintext) > self._limits.max_plaintext_bytes:
            raise SpeechAudioStagingError("speech_reconciliation_artifact_plaintext_limit")
        actual = hashlib.sha256(plaintext).hexdigest()
        if not hmac.compare_digest(actual, task.audio_artifact.content_digest):
            raise SpeechAudioStagingError("speech_reconciliation_artifact_content_tamper")

    def _decode_limits(self, task: SpeechReconciliationWorkerTask) -> AudioDecodeLimits:
        return AudioDecodeLimits(
            max_encoded_bytes=min(task.audio_artifact.plaintext_bytes, self._limits.max_plaintext_bytes),
            max_decoded_pcm_bytes=min(task.audio_artifact.decoded_pcm_bytes, self._limits.max_decoded_pcm_bytes),
            max_duration_ms=min(task.audio_artifact.duration_ms, self._limits.max_duration_ms),
            max_channels=2,
            max_sample_rate_hz=96_000,
            target_sample_rate_hz=16_000,
            ffmpeg_timeout_sec=min(
                self._limits.decoder_timeout_seconds,
                max(1, task.budget_ledger.remaining.wall_time_ms // 1000),
            ),
        )

    def _verify_authority(self, task: SpeechReconciliationWorkerTask, phase: str) -> None:
        active, reason = self._authority.verify(task, phase=phase)
        if not active:
            safe = str(reason or "speech_reconciliation_authority_denied")
            if not safe.startswith("speech_reconciliation_"):
                safe = "speech_reconciliation_authority_denied"
            raise SpeechAudioStagingError(safe)

    @staticmethod
    def _check_cancel(check: CancellationCheck | None) -> None:
        if check is None:
            return
        try:
            check()
        except SpeechAudioStagingError:
            raise
        except Exception as exc:
            raise SpeechAudioStagingError("speech_reconciliation_cancelled") from exc

    def _new_workspace(self, task: SpeechReconciliationWorkerTask) -> Path:
        suffix = f"{task.job.job_id[:24]}-{task.job.attempt_id[:24]}-{uuid.uuid4().hex}"
        path = self._root / f"{_WORKSPACE_PREFIX}{suffix}"
        try:
            os.mkdir(path, 0o700)
        except OSError as exc:
            raise SpeechAudioStagingError("speech_reconciliation_workspace_unavailable", retryable=True) from exc
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
            _remove_workspace(path)
            raise SpeechAudioStagingError("speech_reconciliation_workspace_boundary_violation")
        return path


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
            raise SpeechAudioStagingError("speech_reconciliation_workspace_boundary_violation")
        if root.exists() or root.is_symlink():
            metadata = root.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise SpeechAudioStagingError("speech_reconciliation_workspace_boundary_violation")
        else:
            root.mkdir(mode=0o700)
        root.chmod(0o700)
    except SpeechAudioStagingError:
        raise
    except OSError as exc:
        raise SpeechAudioStagingError("speech_reconciliation_workspace_unavailable", retryable=True) from exc
    return root


def _remove_workspace(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path)


def _safe_decode_reason(code: str) -> str:
    if code in {"audio.format_unsupported", "validation.unsupported_audio"}:
        return "speech_reconciliation_audio_format_unsupported"
    normalized = "".join(character if character.isalnum() else "_" for character in str(code).lower())[:80]
    return f"speech_reconciliation_{normalized or 'decoder_failed'}"


__all__ = [
    "AesGcmSpeechArtifactDecryptor",
    "EpochKeyring",
    "SpeechArtifactDecryptor",
    "SpeechArtifactKeyResolver",
    "SpeechAudioStager",
    "SpeechAudioStagingError",
    "SpeechAudioStagingLimits",
    "SpeechStageAuthority",
    "StagedSpeechAudio",
    "derive_artifact_key",
]
