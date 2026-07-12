from __future__ import annotations

from io import BytesIO

from voice_runtime.app import create_app
from voice_runtime.backends.router import build_voice_backend_router
from voice_runtime.config import VoiceRuntimeConfig
from voice_runtime.context import VoiceRecognitionContext
from voice_runtime.pipeline import TranscriptionPipeline


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
    postprocess = result.stages[-1]
    assert postprocess["segments"][0]["original_text"]
    assert postprocess["segments"][0]["applied_text"] == result.segments[0].text
    assert postprocess["segments"][0]["edits"]
    assert result.decision_trace["postprocessing"]["processor"] == "rules"


def test_confidence_rerun_never_falls_back_to_unbounded_full_audio():
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
    assert result.rerun_backend is None
    stage = next(stage for stage in result.stages if stage["stage"] == "confidence_rerun")
    assert stage["rerun_count"] == 0
    assert stage["status"] == "skipped"
    assert stage["full_audio_fallback"] is False
    assert "confidence_rerun_pcm_unavailable" in result.warnings


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
    assert "review_required" in postprocess
    assert all("proposed_text" in segment for segment in postprocess["segments"])


def test_profile_substitution_is_applied_only_from_valid_bounded_snapshot():
    config = VoiceRuntimeConfig(
        backend_fallback_order=("mock",),
        transcription_pipeline="simple",
        asr_backend="mock",
        primary_backend="mock",
        postprocess_backend="rules",
    )
    pipeline = TranscriptionPipeline(config=config, backend=build_voice_backend_router(config))
    context = VoiceRecognitionContext.from_mapping(
        {
            "personalization": {
                "version": 3,
                "consent_id": "consent-a",
                "consent_version": 2,
                "consent_granted": True,
                "revocation_epoch": 2,
                "expires_at": 4_000_000_000,
                "vocabulary": [],
                "substitutions": [{"source": "mock transcript", "target": "profile transcript"}],
                "preferences": [],
                "weights": {"substitution": 1.0, "preference": 0.75, "vocabulary": 1.0},
                "persistence_owner": "hub",
                "runtime_persistence_allowed": False,
            }
        }
    )

    result = pipeline.transcribe(filename="sample.webm", content=b"audio-bytes", context=context)

    assert result.text.startswith("Profile transcript")
    stage = next(item for item in result.stages if item["stage"] == "postprocess")
    assert stage["personalization_snapshot_version"] == "3"
    assert stage["personalization_consent_reference"] == "consent-a"
    assert stage["personalization_substitution_count"] == 1


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
