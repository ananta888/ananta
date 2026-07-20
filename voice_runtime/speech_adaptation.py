"""Receiver-local speech adapter activation and deterministic fallback."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from agent.services.ml_intern_speech_adapter_registry import SpeechAdapterRecord
from ananta_contracts.speech_adaptation import speech_scope_digest


class SpeechAdapterInferenceError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SpeechAdapterActivation:
    adapter_id: str
    tenant_id: str
    owner_subject: str
    pair_id: str
    direction: str
    speaker_digest: str
    base_model_id: str
    base_model_digest: str
    consent_digest: str


@dataclass(frozen=True)
class LoadedSpeechAdapter:
    adapter_id: str
    artifact_sha256: str
    handle: Any


@dataclass(frozen=True)
class SpeechInferenceResult:
    mode: str
    audio: bytes
    reason_code: str | None
    adapter_id: str | None


class SpeechAdapterRegistryReadPort(Protocol):
    def get_for_pair(
        self,
        adapter_id: str,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
    ) -> SpeechAdapterRecord: ...


class ReceiverSpeechAdapterLoader(Protocol):
    def load(self, *, artifact_ref: str, expected_sha256: str, base_model_id: str) -> LoadedSpeechAdapter: ...

    def infer(self, loaded: LoadedSpeechAdapter, semantic_payload: bytes) -> bytes: ...

    def unload(self, loaded: LoadedSpeechAdapter) -> None: ...

    def clear_artifact_cache(self, artifact_sha256: str) -> None: ...


class BaseSpeechReconstructor(Protocol):
    def reconstruct(self, semantic_payload: bytes) -> bytes: ...


@dataclass
class _CacheEntry:
    activation: SpeechAdapterActivation
    record_version: int
    loaded: LoadedSpeechAdapter


class ReceiverLocalSpeechAdaptationRuntime:
    """Loads approved adapters locally and never exposes their handle."""

    def __init__(
        self,
        *,
        registry: SpeechAdapterRegistryReadPort,
        loader: ReceiverSpeechAdapterLoader,
        base: BaseSpeechReconstructor,
        max_loaded_adapters: int = 2,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ) -> None:
        if not 1 <= max_loaded_adapters <= 16:
            raise ValueError("receiver speech adapter cache limit is invalid")
        self._registry = registry
        self._loader = loader
        self._base = base
        self._limit = max_loaded_adapters
        self._clock_ms = clock_ms
        self._cache: OrderedDict[tuple[str, str, str], _CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    def activate(self, request: SpeechAdapterActivation) -> None:
        record = self._resolve_current(request)
        key = (request.pair_id, request.direction, request.adapter_id)
        with self._lock:
            existing = self._cache.get(key)
            if (
                existing is not None
                and existing.record_version == record.registry_version
                and secrets.compare_digest(existing.loaded.artifact_sha256, record.artifact_sha256)
            ):
                self._cache.move_to_end(key)
                return
            if existing is not None:
                self._evict(key)
            try:
                loaded = self._loader.load(
                    artifact_ref=record.artifact_ref,
                    expected_sha256=record.artifact_sha256,
                    base_model_id=record.base_model_id,
                )
            except Exception as exc:
                self._loader.clear_artifact_cache(record.artifact_sha256)
                raise SpeechAdapterInferenceError(
                    "speech_adapter_load_failed",
                    "receiver-local speech adapter load failed",
                ) from exc
            if (
                loaded.adapter_id != record.adapter_id
                or not secrets.compare_digest(loaded.artifact_sha256, record.artifact_sha256)
            ):
                self._safe_unload(loaded)
                self._loader.clear_artifact_cache(record.artifact_sha256)
                raise SpeechAdapterInferenceError(
                    "speech_adapter_load_binding_mismatch",
                    "loaded speech adapter does not match the registry",
                )
            try:
                # Loading model weights is the longest untrusted interval in
                # activation. Re-read the Hub-owned registry after it and
                # fence the exact version/artifact before publishing a handle
                # into the receiver cache. A revoke or rollback that races the
                # load therefore cannot authorize even one stale inference.
                current = self._resolve_current(request)
                if not self._same_activation_authority(record, current):
                    raise SpeechAdapterInferenceError(
                        "speech_adapter_authority_changed",
                        "speech adapter authority changed while loading",
                    )
            except Exception as exc:
                self._safe_unload(loaded)
                self._loader.clear_artifact_cache(record.artifact_sha256)
                if isinstance(exc, SpeechAdapterInferenceError):
                    raise
                raise SpeechAdapterInferenceError(
                    "speech_adapter_authority_changed",
                    "speech adapter authority could not be revalidated after loading",
                ) from exc
            self._cache[key] = _CacheEntry(request, record.registry_version, loaded)
            self._cache.move_to_end(key)
            while len(self._cache) > self._limit:
                oldest = next(iter(self._cache))
                self._evict(oldest)

    def infer(
        self,
        request: SpeechAdapterActivation,
        semantic_payload: bytes,
        *,
        ordinary_audio: bytes | None = None,
    ) -> SpeechInferenceResult:
        if not isinstance(semantic_payload, bytes) or len(semantic_payload) > 8 * 1024**2:
            return self._fallback(
                semantic_payload if isinstance(semantic_payload, bytes) else b"",
                ordinary_audio,
                "speech_payload_invalid",
            )
        key = (request.pair_id, request.direction, request.adapter_id)
        try:
            current = self._resolve_current(request)
            with self._lock:
                entry = self._cache.get(key)
                if entry is None:
                    self.activate(request)
                    entry = self._cache[key]
                if entry.record_version != current.registry_version or not secrets.compare_digest(
                    entry.loaded.artifact_sha256,
                    current.artifact_sha256,
                ):
                    self._evict(key)
                    self.activate(request)
                    entry = self._cache[key]
                self._cache.move_to_end(key)
                loaded = entry.loaded
                loaded_version = entry.record_version
            before_inference = self._resolve_current(request)
            if (
                before_inference.registry_version != loaded_version
                or not secrets.compare_digest(
                    before_inference.artifact_sha256,
                    loaded.artifact_sha256,
                )
            ):
                raise SpeechAdapterInferenceError(
                    "speech_adapter_authority_changed",
                    "speech adapter authority changed before inference",
                )
            audio = self._loader.infer(loaded, semantic_payload)
            if not isinstance(audio, bytes) or not audio:
                raise SpeechAdapterInferenceError("speech_adapter_quality_failed", "adapter produced no audio")
            after_inference = self._resolve_current(request)
            if (
                after_inference.registry_version != loaded_version
                or not secrets.compare_digest(
                    after_inference.artifact_sha256,
                    loaded.artifact_sha256,
                )
            ):
                raise SpeechAdapterInferenceError(
                    "speech_adapter_authority_changed",
                    "speech adapter authority changed during inference",
                )
            return SpeechInferenceResult("adapted", audio, None, request.adapter_id)
        except Exception as exc:
            reason = str(getattr(exc, "reason_code", "speech_adapter_runtime_failed"))[:128]
            with self._lock:
                if key in self._cache:
                    self._evict(key)
            return self._fallback(semantic_payload, ordinary_audio, reason)

    def revoke(self, *, pair_id: str, direction: str, adapter_id: str) -> None:
        key = (pair_id, direction, adapter_id)
        with self._lock:
            if key in self._cache:
                self._evict(key)

    def cleanup_expired(self) -> int:
        removed = 0
        with self._lock:
            for key, entry in list(self._cache.items()):
                try:
                    self._resolve_current(entry.activation)
                except Exception:
                    self._evict(key)
                    removed += 1
        return removed

    def unload_all(self) -> None:
        with self._lock:
            for key in list(self._cache):
                self._evict(key)

    def _resolve_current(self, request: SpeechAdapterActivation) -> SpeechAdapterRecord:
        record = self._registry.get_for_pair(
            request.adapter_id,
            tenant_id=request.tenant_id,
            owner_subject=request.owner_subject,
            pair_id=request.pair_id,
            direction=request.direction,
        )
        expected_scope = speech_scope_digest(
            pair_id=request.pair_id,
            direction=request.direction,
            speaker_digest=request.speaker_digest,
        )
        now = int(self._clock_ms())
        checks = (
            (record.status == "approved", "speech_adapter_not_approved"),
            (record.scope_digest == expected_scope, "speech_adapter_scope_mismatch"),
            (record.speaker_digest == request.speaker_digest, "speech_adapter_speaker_mismatch"),
            (record.base_model_id == request.base_model_id, "speech_adapter_base_model_mismatch"),
            (record.base_model_digest == request.base_model_digest, "speech_adapter_base_model_mismatch"),
            (record.consent_digest == request.consent_digest, "speech_adapter_consent_mismatch"),
            (now < record.consent_expires_at_ms, "speech_adapter_consent_expired"),
            (now < record.expires_at_ms, "speech_adapter_expired"),
        )
        for passed, reason in checks:
            if not passed:
                raise SpeechAdapterInferenceError(reason, "speech adapter activation binding is invalid")
        return record

    def _fallback(
        self,
        semantic_payload: bytes,
        ordinary_audio: bytes | None,
        reason_code: str,
    ) -> SpeechInferenceResult:
        try:
            audio = self._base.reconstruct(semantic_payload)
            if isinstance(audio, bytes) and audio:
                return SpeechInferenceResult("base", audio, reason_code, None)
        except Exception:
            pass
        if isinstance(ordinary_audio, bytes) and ordinary_audio:
            return SpeechInferenceResult("ordinary_audio", ordinary_audio, reason_code, None)
        return SpeechInferenceResult("unavailable", b"", reason_code, None)

    def _evict(self, key: tuple[str, str, str]) -> None:
        entry = self._cache.pop(key)
        self._safe_unload(entry.loaded)
        self._loader.clear_artifact_cache(entry.loaded.artifact_sha256)

    def _safe_unload(self, loaded: LoadedSpeechAdapter) -> None:
        try:
            self._loader.unload(loaded)
        except Exception:
            # Cache deletion remains mandatory even if backend unload reports a
            # failure; no stale handle stays addressable from this runtime.
            pass

    @staticmethod
    def _same_activation_authority(
        before: SpeechAdapterRecord,
        after: SpeechAdapterRecord,
    ) -> bool:
        return (
            before.adapter_id == after.adapter_id
            and before.registry_version == after.registry_version
            and before.status == after.status == "approved"
            and before.artifact_ref == after.artifact_ref
            and secrets.compare_digest(before.artifact_sha256, after.artifact_sha256)
            and before.artifact_size_bytes == after.artifact_size_bytes
            and secrets.compare_digest(before.scope_digest, after.scope_digest)
            and before.base_model_id == after.base_model_id
            and secrets.compare_digest(before.base_model_digest, after.base_model_digest)
            and secrets.compare_digest(before.consent_digest, after.consent_digest)
            and before.consent_expires_at_ms == after.consent_expires_at_ms
            and before.expires_at_ms == after.expires_at_ms
        )
