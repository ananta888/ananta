#!/usr/bin/env python3
"""Deterministic semantic-speech runtime acceptance gate.

The simulation uses production state machines and ports.  It contains no raw
audio or transcript corpus and performs no network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_runtime.backends.base import TranscriptionCandidate  # noqa: E402
from voice_runtime.source_correction import (  # noqa: E402
    SourceCorrectionRequest,
    SourceCorrectionService,
)
from voice_runtime.speech_quality_policy import (  # noqa: E402
    SpeechQualityPolicy,
    SpeechQualitySample,
    classify_speech_transport_failure,
)
from voice_runtime.streaming import StreamingTranscriptTracker  # noqa: E402

ARTIFACT = ROOT / "artifacts/test-gates/semantic-speech-runtime.json"
SOURCE_FILES = (
    "ananta_contracts/semantic_speech.py",
    "voice_runtime/streaming.py",
    "voice_runtime/source_correction.py",
    "agent/services/semantic_speech_source_correction_service.py",
    "agent/routes/voice.py",
    "voice_runtime/speech_quality_policy.py",
    "schemas/webrtc/speech_transport_contract.v1.json",
    "schemas/webrtc/speech_semantic_frame.v1.json",
    "schemas/webrtc/speech_correction.v1.json",
    "frontend-angular/src/app/services/semantic-speech-crypto.service.ts",
    "frontend-angular/src/app/services/semantic-speech-transport.service.ts",
    "frontend-angular/src/app/services/speech-delay-buffer.service.ts",
    "frontend-angular/src/app/services/semantic-speech-source-correction-api.service.ts",
    "frontend-angular/src/app/services/semantic-speech-runtime-coordinator.service.ts",
    "frontend-angular/src/app/services/speech-transcript-revision.store.ts",
    "frontend-angular/src/app/services/semantic-speech-quality-controller.service.ts",
    "frontend-angular/src/app/services/webrtc-media-health.service.ts",
    "frontend-angular/src/app/features/voice/semantic-speech-panel.component.ts",
    "frontend-angular/src/app/features/voice/semantic-media-program.facade.ts",
    "frontend-angular/src/app/features/voice/semantic-media-program-shell.component.ts",
    "frontend-angular/src/app/features/voice/reconstruction/generic-speech-reconstructor.service.ts",
)
SEGMENTS = 480


class _RetentionProbe:
    def __init__(self) -> None:
        self.released: set[tuple[str, str]] = set()

    def release(self, *, session_id: str, source_digest: str, reason_code: str) -> None:
        del reason_code
        self.released.add((session_id, source_digest))


def _sample(measured_at_ms: int, *, queue_bytes: int = 0) -> SpeechQualitySample:
    return SpeechQualitySample(
        measured_at_ms=measured_at_ms,
        loss_ratio=0.0,
        queue_bytes=queue_bytes,
        partial_age_ms=10,
        correction_lag_ms=100,
        source_loss_ratio=0.0,
        feature_loss_ratio=0.0,
        reconstruction_error_ratio=0.0,
    )


def build_report() -> dict[str, object]:
    transcript = StreamingTranscriptTracker(live_partials=True, max_turns=512)
    retention = _RetentionProbe()
    correction = SourceCorrectionService(retention)
    emitted_partials = 0
    emitted_finals = 0
    duplicate_finals = 0
    correction_attempts = 0
    correction_failures = 0
    max_partial_delivery_ms = 0
    source_digest = "a" * 64

    for sequence in range(SEGMENTS):
        turn_id = f"turn-{sequence}"
        observed_at_ms = 10_000 + sequence * 60_000
        partial = transcript.ingest(
            turn_id=turn_id,
            revision=1,
            text=f"segment {sequence} provisional",
            final=False,
            observed_at_ms=observed_at_ms,
        )
        if partial is not None:
            emitted_partials += 1
            max_partial_delivery_ms = max(max_partial_delivery_ms, partial.emitted_at_ms - observed_at_ms)
        final = transcript.ingest(
            turn_id=turn_id,
            revision=2,
            text=f"segment {sequence} final",
            final=True,
            segment_closed=True,
            observed_at_ms=observed_at_ms + 1_000,
        )
        if final is not None:
            emitted_finals += 1
        duplicate = transcript.ingest(
            turn_id=turn_id,
            revision=2,
            text=f"segment {sequence} final",
            final=True,
            segment_closed=True,
            observed_at_ms=observed_at_ms + 1_001,
        )
        if duplicate is None:
            duplicate_finals += 1

        provisional = TranscriptionCandidate(
            candidate_id=f"live-{sequence}",
            backend="semantic-live",
            text=f"segment {sequence} provisional",
        )
        source = TranscriptionCandidate(
            candidate_id=f"source-{sequence}",
            backend="source-asr",
            text=f"segment {sequence} final corrected",
            source_audio_digest=source_digest,
        )
        result = correction.correct(
            request=SourceCorrectionRequest(
                session_id=f"session-{sequence}",
                epoch=1,
                turn_id=turn_id,
                provisional_revision=2,
                consent_version=1,
                source_digest=source_digest,
                source_expires_at_ms=observed_at_ms + 120_000,
                deadline_at_ms=observed_at_ms + 30_000,
                requested_at_ms=observed_at_ms + 1_000,
            ),
            provisional=provisional,
            source=source,
        )
        correction_attempts += int(result.correction_attempted)
        correction_failures += int(result.authority not in {"corrected", "final"})

    tracker_snapshot = transcript.snapshot()
    quality = SpeechQualityPolicy(initial_mode="semantic_reconstruction")
    backpressure = quality.evaluate(
        _sample(10_000, queue_bytes=3 * 1024 * 1024 + 1),
        desired_mode="semantic_reconstruction",
    )
    hysteresis_hold = quality.evaluate(_sample(12_000), desired_mode="semantic_reconstruction")
    recovered = quality.evaluate(_sample(15_000), desired_mode="semantic_reconstruction")
    revoked = quality.evaluate(_sample(15_001), desired_mode="semantic_reconstruction", revoked=True)
    failures = {str(status): asdict(classify_speech_transport_failure(status)) for status in (404, 409, 413)}

    metrics = {
        "simulated_segments": SEGMENTS,
        "simulated_duration_hours": 8,
        "emitted_partials": emitted_partials,
        "emitted_finals": emitted_finals,
        "duplicate_finals_rejected": duplicate_finals,
        "max_partial_delivery_ms": max_partial_delivery_ms,
        "correction_attempts": correction_attempts,
        "correction_failures": correction_failures,
        "source_releases": len(retention.released),
        "tracker_timers": tracker_snapshot["timers"],
        "correction_timers": correction.snapshot()["timers"],
        "backpressure_mode": backpressure.mode,
        "hysteresis_reason": hysteresis_hold.reason_code,
        "recovered_mode": recovered.mode,
        "revoke_mode": revoked.mode,
    }
    checks = {
        "eight_hour_segment_rotation": emitted_finals == SEGMENTS,
        "live_partial_under_250ms": emitted_partials == SEGMENTS and max_partial_delivery_ms <= 250,
        "reconnect_duplicate_safe": duplicate_finals == SEGMENTS,
        "correction_after_every_segment": correction_attempts == SEGMENTS and correction_failures == 0,
        "source_deleted_after_correction": len(retention.released) == SEGMENTS,
        "backpressure_falls_back_to_ordinary_audio": backpressure.mode == "ordinary_audio",
        "five_second_hysteresis": (
            hysteresis_hold.reason_code == "quality_hysteresis_hold" and recovered.mode == "semantic_reconstruction"
        ),
        "revoke_immediate": revoked.mode == "ordinary_audio" and revoked.reason_code == "consent_revoked",
        "stream_404_purges": failures["404"]["purge_session"] is True,
        "stop_409_purges": failures["409"]["purge_session"] is True,
        "chunk_413_drops_and_reduces": (
            failures["413"]["drop_current_segment"] is True and failures["413"]["reduce_segment_size"] is True
        ),
        "no_runtime_timers": tracker_snapshot["timers"] == 0 and correction.snapshot()["timers"] == 0,
        "ordinary_audio_remains_available": all(
            item.ordinary_audio_available for item in (backpressure, hysteresis_hold, recovered, revoked)
        ),
    }
    return {
        "schema_version": "ananta.semantic-speech-runtime-gate.v1",
        "gate": "semantic_speech_runtime",
        "contains_media_or_transcript_data": False,
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCE_FILES},
        "metrics": metrics,
        "failure_actions": failures,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    report = build_report()
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.write:
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(encoded)
    elif args.verify:
        if not ARTIFACT.exists() or ARTIFACT.read_text() != encoded:
            print("semantic speech runtime gate artifact is missing or stale")
            return 1
    print(encoded, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
