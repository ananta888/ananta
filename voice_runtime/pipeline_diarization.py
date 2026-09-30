"""Speaker-diarization stage of the transcription pipeline.

It owns the lazily initialised offline diarization adapter; model loading is
local-only and never downloads.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .backends.base import TranscriptionResult
from .config import VoiceRuntimeConfig
from .diarization import build_diarization_processor
from .diarization_adapters import (
    LocalDiarizationAdapter,
    PyannoteDiarizationAdapter,
    SafeDiarizationProcessor,
    load_offline_diarization_manifest,
)
from .execution_policy import VoiceExecutionPolicy
from .preprocessing import DecodedPcmAudio


class DiarizationStage:
    """Assign speakers to result segments according to the execution policy."""

    def __init__(
        self,
        *,
        config: VoiceRuntimeConfig,
        adapter: LocalDiarizationAdapter | None = None,
    ) -> None:
        self._config = config
        self._adapter = adapter
        self._adapter_initialized = adapter is not None

    def apply(
        self,
        result: TranscriptionResult,
        *,
        decoded_audio: DecodedPcmAudio | None,
        policy: VoiceExecutionPolicy,
    ) -> tuple[TranscriptionResult, dict | None]:
        if policy.diarization_backend == "pyannote":
            if decoded_audio is None:
                return result.with_additional_warnings(["diarization_input_unavailable"]), {
                    "stage": "diarization",
                    "backend": "pyannote",
                    "status": "skipped",
                    "reason_code": "decoded_audio_unavailable",
                }
            adapter = self.resolve_adapter()
            if adapter is None:
                return result.with_additional_warnings(["diarization_unavailable"]), {
                    "stage": "diarization",
                    "backend": "pyannote",
                    "status": "skipped",
                    "reason_code": "adapter_unavailable",
                }
            outcome = SafeDiarizationProcessor(adapter).process(audio=decoded_audio, segments=result.segments)
            warnings = list(result.warnings)
            if outcome.status != "succeeded":
                warnings.append("diarization_unavailable")
            return replace(result, segments=outcome.segments, warnings=tuple(warnings)), {
                "stage": "diarization",
                "backend": outcome.adapter_id,
                "status": outcome.status,
                "reason_code": outcome.reason_code,
                "segment_count": len(result.segments),
            }
        processor = build_diarization_processor(policy.diarization_backend)
        if processor is None:
            return result, None
        return replace(result, segments=processor.assign(result.segments)), {
            "stage": "diarization",
            "backend": processor.name(),
            "segment_count": len(result.segments),
        }

    def resolve_adapter(self) -> LocalDiarizationAdapter | None:
        if self._adapter_initialized:
            return self._adapter
        self._adapter_initialized = True
        if not self._config.pyannote_manifest_path or not self._config.diarization_model_root:
            return None
        try:
            manifest = load_offline_diarization_manifest(self._config.pyannote_manifest_path)
            self._adapter = PyannoteDiarizationAdapter(
                manifest=manifest,
                allowed_model_roots=(Path(self._config.diarization_model_root),),
            )
        except Exception:
            self._adapter = None
        return self._adapter
