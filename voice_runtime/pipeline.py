from __future__ import annotations

from dataclasses import replace

from .backends.base import TranscriptionResult, TranscriptionSegment, VoiceBackend
from .backends.mock import MockVoiceBackend
from .backends.voxtral import VoxtralBackend
from .backends.vosk_backend import VoskBackend
from .backends.whisper_cpp import WhisperCppBackend
from .config import VoiceRuntimeConfig
from .diarization import build_diarization_processor
from .glossary import Glossary
from .postprocessing import build_postprocessor
from .preprocessing import build_vad_processor


class TranscriptionPipeline:
    """Configurable Voice Runtime transcription orchestrator."""

    def __init__(self, *, config: VoiceRuntimeConfig, backend: VoiceBackend) -> None:
        self._config = config
        self._backend = backend

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        pipeline = self._config.transcription_pipeline
        if pipeline == "simple":
            result = self._backend.transcribe(filename=filename, content=content, language=language)
            return self._ensure_metadata(result, pipeline=pipeline, stages=({"stage": "asr", "backend": result.raw_backend or self._backend.name()},))

        vad = build_vad_processor(self._config.vad_backend)
        audio_segments = vad.split(filename=filename, content=content)
        stages: list[dict] = [
            {"stage": "vad", "backend": vad.name(), "segment_count": len(audio_segments)},
        ]
        asr_backend = self._select_pipeline_backend(pipeline)
        results = [
            asr_backend.transcribe(filename=segment.filename, content=segment.content, language=language)
            for segment in audio_segments
        ]
        result = self._merge_results(results=results, filename=filename, fallback_language=language)
        stages.append({"stage": "asr", "backend": result.raw_backend or asr_backend.name(), "segment_count": len(result.segments)})

        result, rerun_stage = self._maybe_rerun_low_confidence(result, filename=filename, content=content, language=language)
        if rerun_stage:
            stages.append(rerun_stage)

        result, diarization_stage = self._maybe_diarize(result)
        if diarization_stage:
            stages.append(diarization_stage)

        result, postprocess_stage = self._maybe_postprocess(result)
        if postprocess_stage:
            stages.append(postprocess_stage)

        return self._ensure_metadata(result, pipeline=pipeline, stages=tuple(stages))

    def _select_pipeline_backend(self, pipeline: str) -> VoiceBackend:
        if pipeline == "oldschool_light":
            return self._backend_for_id(self._config.asr_backend)
        if pipeline == "whisper_cpp":
            return self._backend_for_id("whisper_cpp")
        if pipeline in {"meeting", "confidence_rerun", "custom", "realtime_streaming"}:
            return self._backend_for_id(self._config.asr_backend)
        return self._backend

    def _backend_for_id(self, backend_id: str) -> VoiceBackend:
        normalized = str(backend_id or "mock").strip().lower()
        if normalized == "mock":
            return MockVoiceBackend(model=f"mock-{self._config.model}")
        if normalized == "voxtral":
            return VoxtralBackend(
                model=self._config.model,
                fallback_model=self._config.fallback_model,
                preferred_device=self._config.device,
                model_path=self._config.model_path,
            )
        if normalized == "vosk":
            return VoskBackend(model_path=self._config.vosk_model_path)
        if normalized == "whisper_cpp":
            return WhisperCppBackend(
                binary=self._config.whisper_cpp_bin,
                model_path=self._config.whisper_cpp_model_path,
                extra_args=self._config.whisper_cpp_extra_args,
                timeout_sec=self._config.timeout_sec,
            )
        raise ValueError(f"unsupported ASR backend: {normalized}")

    def _merge_results(
        self,
        *,
        results: list[TranscriptionResult],
        filename: str,
        fallback_language: str | None,
    ) -> TranscriptionResult:
        if not results:
            return TranscriptionResult(text="", language=fallback_language or "und", model=self._config.model, warnings=("pipeline_no_segments",))
        warnings: list[str] = []
        segments: list[TranscriptionSegment] = []
        offset_ms = 0
        for result in results:
            warnings.extend(result.warnings)
            if result.segments:
                segments.extend(result.segments)
            else:
                duration = result.duration_ms or max(50, len(result.text) * 2)
                segments.append(
                    TranscriptionSegment(
                        start_ms=offset_ms,
                        end_ms=offset_ms + duration,
                        text=result.text,
                        confidence=result.confidence,
                        backend=result.raw_backend or result.model,
                    )
                )
            offset_ms = max(offset_ms, result.duration_ms or 0)
        text = " ".join(segment.text for segment in segments if segment.text).strip()
        confidences = [segment.confidence for segment in segments if segment.confidence is not None]
        return TranscriptionResult(
            text=text or f"transcript ({filename or 'audio'})",
            language=results[0].language or fallback_language or "und",
            duration_ms=max((segment.end_ms for segment in segments), default=results[0].duration_ms),
            model=results[0].model or self._config.model,
            warnings=tuple(warnings),
            segments=tuple(segments),
            confidence=(sum(confidences) / len(confidences)) if confidences else results[0].confidence,
            raw_backend=results[0].raw_backend or results[0].model,
        )

    def _maybe_rerun_low_confidence(
        self,
        result: TranscriptionResult,
        *,
        filename: str,
        content: bytes,
        language: str | None,
    ) -> tuple[TranscriptionResult, dict | None]:
        enabled = self._config.confidence_rerun_enabled or self._config.transcription_pipeline == "confidence_rerun"
        if not enabled or self._config.rerun_max_segments <= 0:
            return result, None
        low = [
            segment
            for segment in result.segments
            if segment.confidence is not None and segment.confidence < self._config.confidence_threshold
        ][: self._config.rerun_max_segments]
        if not low:
            return result, {"stage": "confidence_rerun", "backend": self._config.rerun_backend, "rerun_count": 0}
        try:
            rerun = self._backend_for_id(self._config.rerun_backend).transcribe(filename=filename, content=content, language=language)
        except Exception as exc:
            return (
                result.with_additional_warnings([f"confidence_rerun_failed:{exc}"]),
                {"stage": "confidence_rerun", "backend": self._config.rerun_backend, "rerun_count": 0, "error": str(exc)},
            )
        merged = replace(
            rerun,
            warnings=tuple([*result.warnings, *rerun.warnings, "confidence_rerun_applied"]),
            pipeline=result.pipeline,
            raw_backend=result.raw_backend,
            rerun_backend=rerun.raw_backend or self._config.rerun_backend,
        )
        return merged, {"stage": "confidence_rerun", "backend": self._config.rerun_backend, "rerun_count": len(low)}

    def _maybe_diarize(self, result: TranscriptionResult) -> tuple[TranscriptionResult, dict | None]:
        processor = build_diarization_processor(self._config.diarization_backend)
        if processor is None:
            return result, None
        return replace(result, segments=processor.assign(result.segments)), {
            "stage": "diarization",
            "backend": processor.name(),
            "segment_count": len(result.segments),
        }

    def _maybe_postprocess(self, result: TranscriptionResult) -> tuple[TranscriptionResult, dict | None]:
        glossary = Glossary.load(self._config.glossary_path)
        processor = build_postprocessor(self._config.postprocess_backend, glossary=glossary)
        if processor is None:
            if glossary.warnings:
                return result.with_additional_warnings(list(glossary.warnings)), None
            return result, None
        processed = processor.process(result.text)
        return replace(
            result,
            text=processed.text,
            warnings=tuple([*result.warnings, *processed.warnings]),
        ), {
            "stage": "postprocess",
            "backend": processor.name(),
            "changed": processed.changed,
            "llm_used": processor.name() == "llm",
            **glossary.as_stage_metadata(),
        }

    @staticmethod
    def _ensure_metadata(
        result: TranscriptionResult,
        *,
        pipeline: str,
        stages: tuple[dict, ...],
    ) -> TranscriptionResult:
        segments = result.segments
        if not segments and result.text:
            segments = (
                TranscriptionSegment(
                    start_ms=0,
                    end_ms=result.duration_ms or max(50, len(result.text) * 2),
                    text=result.text,
                    confidence=result.confidence,
                    backend=result.raw_backend or result.model,
                ),
            )
        confidences = [segment.confidence for segment in segments if segment.confidence is not None]
        confidence = result.confidence
        if confidence is None and confidences:
            confidence = sum(confidences) / len(confidences)
        return replace(
            result,
            segments=segments,
            pipeline=result.pipeline or pipeline,
            confidence=confidence,
            raw_backend=result.raw_backend or result.model,
            stages=tuple([*result.stages, *stages]),
        )
