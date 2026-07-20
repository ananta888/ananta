from __future__ import annotations

from dataclasses import replace

import pytest

from voice_runtime.speech_quality_policy import (
    SpeechQualityPolicy,
    SpeechQualitySample,
    classify_speech_transport_failure,
)


def _sample(measured_at_ms: int = 10_000, **changes: object) -> SpeechQualitySample:
    value = SpeechQualitySample(
        measured_at_ms=measured_at_ms,
        loss_ratio=0.01,
        queue_bytes=1_000,
        partial_age_ms=100,
        correction_lag_ms=1_000,
        source_loss_ratio=0.0,
        feature_loss_ratio=0.0,
        reconstruction_error_ratio=0.0,
    )
    return replace(value, **changes)


@pytest.mark.parametrize(
    ("changes", "expected_mode", "reason"),
    [
        ({"loss_ratio": 0.081}, "ordinary_audio", "packet_loss_high"),
        ({"queue_bytes": 3 * 1024 * 1024 + 1}, "ordinary_audio", "speech_queue_high"),
        ({"partial_age_ms": 1_001}, "ordinary_audio", "live_partial_stale"),
        ({"correction_lag_ms": 120_001}, "transcript_live", "correction_lag_high"),
        ({"source_loss_ratio": 0.21}, "transcript_live", "source_loss_high"),
        ({"feature_loss_ratio": 0.26}, "transcript_live", "feature_loss_high"),
        ({"reconstruction_error_ratio": 0.21}, "transcript_live", "reconstruction_quality_low"),
    ],
)
def test_fixed_thresholds_degrade_deterministically(
    changes: dict[str, object], expected_mode: str, reason: str
) -> None:
    policy = SpeechQualityPolicy(initial_mode="semantic_reconstruction")
    decision = policy.evaluate(_sample(**changes), desired_mode="semantic_reconstruction")
    assert decision.mode == expected_mode
    assert decision.reason_code == reason
    assert decision.ordinary_audio_available


def test_hysteresis_limits_normal_mode_changes_to_one_per_five_seconds() -> None:
    policy = SpeechQualityPolicy(initial_mode="ordinary_audio")
    first = policy.evaluate(_sample(10_000), desired_mode="semantic_reconstruction")
    held = policy.evaluate(_sample(12_000, feature_loss_ratio=0.5), desired_mode="semantic_reconstruction")
    next_change = policy.evaluate(_sample(15_000, feature_loss_ratio=0.5), desired_mode="semantic_reconstruction")

    assert first.transitioned and first.mode == "semantic_reconstruction"
    assert first.semantic_features_enabled and first.delayed_source_enabled
    assert not held.transitioned and held.reason_code == "quality_hysteresis_hold"
    assert next_change.transitioned and next_change.mode == "transcript_live"


def test_revoke_and_user_override_bypass_hysteresis_immediately() -> None:
    revoked = SpeechQualityPolicy(initial_mode="semantic_reconstruction").evaluate(
        _sample(), desired_mode="semantic_reconstruction", revoked=True
    )
    overridden = SpeechQualityPolicy(initial_mode="delayed_correction").evaluate(
        _sample(), desired_mode="delayed_correction", user_ordinary_override=True
    )
    assert (revoked.mode, revoked.reason_code) == ("ordinary_audio", "consent_revoked")
    assert (overridden.mode, overridden.reason_code) == ("ordinary_audio", "user_ordinary_override")


def test_http_failure_classes_have_bounded_cleanup_and_retry_actions() -> None:
    assert classify_speech_transport_failure(404).purge_session
    assert classify_speech_transport_failure(409).purge_session
    oversized = classify_speech_transport_failure(413)
    assert oversized.drop_current_segment and oversized.reduce_segment_size and oversized.retry_allowed
    assert not classify_speech_transport_failure(400).retry_allowed


def test_nonfinite_and_negative_measurements_fail_closed() -> None:
    policy = SpeechQualityPolicy()
    with pytest.raises(ValueError, match="ratio"):
        policy.evaluate(_sample(loss_ratio=float("nan")), desired_mode="transcript_live")
    with pytest.raises(ValueError, match="measurement"):
        policy.evaluate(_sample(queue_bytes=-1), desired_mode="transcript_live")
