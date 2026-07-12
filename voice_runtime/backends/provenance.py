from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from ..model_manifest import VoiceModelManifest
from ..resources import BackendResourceRequirement
from .base import ChatResult, TranscriptionResult, VoiceBackend


class ProvenancedVoiceBackend(VoiceBackend):
    def __init__(self, backend: VoiceBackend, manifest: VoiceModelManifest, *, device: str) -> None:
        self._backend = backend
        self._manifest = manifest
        self._device = device

    def name(self) -> str:
        return self._backend.name()

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        result = self._backend.transcribe(filename=filename, content=content, language=language)
        return replace(
            result,
            model=self._manifest.model_id,
            provenance={**dict(result.provenance), **self._manifest.provenance(device=self._device)},
        )

    def transcribe_with_context(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: dict[str, Any],
    ) -> TranscriptionResult:
        method = getattr(self._backend, "transcribe_with_context", None)
        if not callable(method):
            return self.transcribe(filename=filename, content=content, language=language)
        result = cast(
            TranscriptionResult,
            method(filename=filename, content=content, language=language, context=context),
        )
        return replace(
            result,
            model=self._manifest.model_id,
            provenance={**dict(result.provenance), **self._manifest.provenance(device=self._device)},
        )

    def transcribe_with_control(
        self,
        *,
        filename: str,
        content: bytes,
        language: str | None,
        context: dict[str, Any],
        cancellation_token: object,
        deadline_monotonic: float,
    ) -> TranscriptionResult:
        controlled = getattr(self._backend, "transcribe_with_control", None)
        if callable(controlled):
            result = cast(
                TranscriptionResult,
                controlled(
                    filename=filename,
                    content=content,
                    language=language,
                    context=context,
                    cancellation_token=cancellation_token,
                    deadline_monotonic=deadline_monotonic,
                ),
            )
        else:
            contextual = getattr(self._backend, "transcribe_with_context", None)
            result = cast(
                TranscriptionResult,
                contextual(
                    filename=filename,
                    content=content,
                    language=language,
                    context=context,
                )
                if callable(contextual)
                else self._backend.transcribe(
                    filename=filename,
                    content=content,
                    language=language,
                ),
            )
        return replace(
            result,
            model=self._manifest.model_id,
            provenance={
                **dict(result.provenance),
                **self._manifest.provenance(device=self._device),
            },
        )

    def cancel_transcription(self, *, cancellation_token: object) -> None:
        callback = getattr(self._backend, "cancel_transcription", None)
        if callable(callback):
            callback(cancellation_token=cancellation_token)

    def resource_requirements(self) -> BackendResourceRequirement:
        return BackendResourceRequirement(
            ram_bytes=self._manifest.ram_bytes,
            vram_bytes=self._manifest.vram_bytes,
            concurrency_slots=self._manifest.concurrency_slots,
        )

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        return self._backend.audio_chat(filename=filename, content=content, context=context)

    def list_models(self) -> list[dict]:
        models = self._backend.list_models()
        return [
            {
                **item,
                "schema_version": "ananta.model-capability.v1",
                "engine": self._manifest.engine,
                "revision": self._manifest.revision,
                "license": self._manifest.license,
                "quantization": self._manifest.quantization,
                "languages": list(self._manifest.languages),
                "manifest_digest": self._manifest.manifest_digest,
                "synthetic": False,
            }
            for item in models
        ]

    def context_capabilities(self) -> frozenset[str]:
        method = getattr(self._backend, "context_capabilities", None)
        return frozenset(method()) if callable(method) else frozenset()

    def create_incremental_recognizer(self, *, filename: str, language: str | None, max_bytes: int):
        method = getattr(self._backend, "create_incremental_recognizer", None)
        if not callable(method):
            raise RuntimeError("backend does not support incremental recognition")
        return _ProvenancedRecognizer(
            method(filename=filename, language=language, max_bytes=max_bytes),
            manifest=self._manifest,
            device=self._device,
        )


class _ProvenancedRecognizer:
    def __init__(self, recognizer, *, manifest: VoiceModelManifest, device: str) -> None:
        self._recognizer = recognizer
        self._manifest = manifest
        self._device = device

    def accept(self, content: bytes) -> str | None:
        return cast(str | None, self._recognizer.accept(content))

    def finish(self) -> TranscriptionResult:
        result = cast(TranscriptionResult, self._recognizer.finish())
        return replace(
            result,
            model=self._manifest.model_id,
            provenance={**dict(result.provenance), **self._manifest.provenance(device=self._device)},
        )

    def close(self) -> None:
        self._recognizer.close()


def with_manifest(
    backend: VoiceBackend,
    manifest: VoiceModelManifest | None,
    *,
    device: str,
) -> VoiceBackend:
    return ProvenancedVoiceBackend(backend, manifest, device=device) if manifest else backend
