from __future__ import annotations

from io import BytesIO

from voice_runtime.app import create_app
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.pipeline import TranscriptionPipeline
from voice_runtime.backends.router import build_voice_backend_router


def test_oldschool_light_pipeline_emits_vad_and_asr_stages():
    config = VoiceRuntimeConfig(
        backend_fallback_order=("mock",),
        transcription_pipeline="oldschool_light",
        asr_backend="mock",
        postprocess_backend="rules",
    )
    pipeline = TranscriptionPipeline(config=config, backend=build_voice_backend_router(config))

    result = pipeline.transcribe(filename="sample.webm", content=b"audio-bytes", language="de")

    assert result.pipeline == "oldschool_light"
    assert [stage["stage"] for stage in result.stages] == ["vad", "asr", "postprocess"]
    assert result.segments
    assert result.text.endswith(".")


def test_confidence_rerun_pipeline_records_rerun_stage():
    config = VoiceRuntimeConfig(
        backend_fallback_order=("mock",),
        transcription_pipeline="confidence_rerun",
        asr_backend="mock",
        confidence_threshold=0.95,
        rerun_backend="mock",
        rerun_max_segments=1,
    )
    pipeline = TranscriptionPipeline(config=config, backend=build_voice_backend_router(config))

    result = pipeline.transcribe(filename="sample.webm", content=b"audio-bytes")

    assert result.pipeline == "confidence_rerun"
    assert result.rerun_backend == "mock"
    assert any(stage["stage"] == "confidence_rerun" and stage["rerun_count"] == 1 for stage in result.stages)


def test_meeting_pipeline_assigns_mock_speakers():
    config = VoiceRuntimeConfig(
        backend_fallback_order=("mock",),
        transcription_pipeline="meeting",
        asr_backend="mock",
        diarization_backend="mock",
    )
    pipeline = TranscriptionPipeline(config=config, backend=build_voice_backend_router(config))

    result = pipeline.transcribe(filename="meeting.webm", content=b"audio-bytes")

    assert result.segments[0].speaker == "SPEAKER_01"
    assert any(stage["stage"] == "diarization" for stage in result.stages)


def test_llm_postprocess_stage_is_explicitly_marked():
    config = VoiceRuntimeConfig(
        backend_fallback_order=("mock",),
        transcription_pipeline="custom",
        asr_backend="mock",
        postprocess_backend="llm",
    )
    pipeline = TranscriptionPipeline(config=config, backend=build_voice_backend_router(config))

    result = pipeline.transcribe(filename="sample.webm", content=b"audio-bytes")

    postprocess = next(stage for stage in result.stages if stage["stage"] == "postprocess")
    assert postprocess["llm_used"] is True


def test_transcription_route_exposes_additive_pipeline_fields():
    app = create_app(
        VoiceRuntimeConfig(
            backend_fallback_order=("mock",),
            transcription_pipeline="oldschool_light",
            asr_backend="mock",
        )
    )
    client = app.test_client()

    response = client.post(
        "/v1/audio/transcriptions",
        data={"file": (BytesIO(b"audio-bytes"), "sample.webm")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.json["pipeline"] == "oldschool_light"
    assert response.json["raw_backend"] == "mock"
    assert response.json["segments"][0]["backend"] == "mock"
    assert [stage["stage"] for stage in response.json["stages"]][:2] == ["vad", "asr"]
