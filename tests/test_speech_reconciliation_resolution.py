from __future__ import annotations

import time
from pathlib import Path

import pytest

from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationCheckpoint,
)
from tests.speech_reconciliation_support import digest, job_payload
from voice_runtime.backends.base import (
    ChatResult,
    TranscriptionCandidate,
    TranscriptionResult,
    TranscriptionSegment,
)
from voice_runtime.fusion import FusionOutcome
from voice_runtime.peer_transcript_consensus import PeerTranscriptCandidate
from voice_runtime.preprocessing.audio_decode import DecodedPcmAudio
from worker.speech_reconciliation.asr_ensemble import (
    LocalSpeechModel,
    LocalSpeechModelRegistry,
    SpeechAsrEnsemble,
    SpeechAsrEnsembleError,
)
from worker.speech_reconciliation.audio_staging import EpochKeyring, StagedSpeechAudio
from worker.speech_reconciliation.checkpointing import (
    AesGcmSpeechCheckpointCipher,
    SpeechCheckpointError,
    SpeechReconciliationCheckpointStore,
)
from worker.speech_reconciliation.contracts import SpeechReconciliationWorkerTask
from worker.speech_reconciliation.resolver import (
    SpeechReconciliationResolutionError,
    SpeechReconciliationResolver,
)


class _Backend:
    def __init__(self, name: str, text: str, *, confidence: float = 0.9, failure: bool = False, tracker=None) -> None:
        self._name = name
        self._text = text
        self._confidence = confidence
        self._failure = failure
        self._tracker = tracker
        self.calls = 0

    def name(self) -> str:
        return self._name

    def transcribe(self, *, filename: str, content: bytes, language: str | None = None) -> TranscriptionResult:
        del filename, content
        self.calls += 1
        if self._tracker is not None:
            self._tracker.enter()
        try:
            if self._failure:
                raise RuntimeError("private model failure")
            if self._tracker is not None:
                time.sleep(0.03)
            return TranscriptionResult(
                text=self._text,
                language=language,
                duration_ms=100,
                model=self._name,
                confidence=self._confidence,
                segments=(TranscriptionSegment(0, 100, self._text, confidence=self._confidence),),
                provenance={"untrusted": "must be removed"},
            )
        finally:
            if self._tracker is not None:
                self._tracker.leave()

    def audio_chat(self, *, filename: str, content: bytes, context: dict | None = None) -> ChatResult:
        del filename, content, context
        return ChatResult(text="")

    def list_models(self) -> list[dict]:
        return []

    def context_capabilities(self) -> frozenset[str]:
        return frozenset()


class _ConcurrencyTracker:
    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self.active = 0
        self.maximum = 0

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)

    def leave(self) -> None:
        with self._lock:
            self.active -= 1


def _task(
    *,
    passes: list[dict] | None = None,
    cpu_time_ms: int = 60_000,
    gpu_time_ms: int = 60_000,
    checkpoint_bytes: int = 1024 * 1024,
    ledger_sequence: int = 0,
) -> SpeechReconciliationWorkerTask:
    zero = {
        "wall_time_ms": 0,
        "cpu_time_ms": 0,
        "gpu_time_ms": 0,
        "memory_byte_ms": 0,
        "disk_bytes": 0,
        "checkpoint_bytes": 0,
        "energy_millijoules": 0,
    }
    remaining = {
        **zero,
        "wall_time_ms": 60_000,
        "cpu_time_ms": cpu_time_ms,
        "gpu_time_ms": gpu_time_ms,
        "disk_bytes": 16 * 1024 * 1024,
        "checkpoint_bytes": checkpoint_bytes,
    }
    job = job_payload(
        deadline_at_ms=time.time_ns() // 1_000_000 + 60_000,
        source_duration_ms=10_000,
        ledger_sequence=ledger_sequence,
        stage="slow_asr",
    )
    artifact_bytes = 1024
    payload = {
        "contract_version": CONTRACT_VERSION,
        "task_type": "speech_reconciliation_attempt",
        "job": job,
        "budget_ledger": {
            "contract_version": CONTRACT_VERSION,
            "job_id": job["job_id"],
            "attempt_id": job["attempt_id"],
            "fencing_epoch": job["fencing_epoch"],
            "sequence": job["ledger_sequence"],
            "stage": job["stage"],
            "source_duration_ms": job["source_duration_ms"],
            "compute_factor": job["max_compute_factor"],
            "allocated": remaining,
            "reserved": zero,
            "consumed": zero,
            "remaining": remaining,
        },
        "audio_artifact": {
            "artifact_ref": job["input_artifact_ref"],
            "transport_digest": digest("transport"),
            "content_digest": digest("audio"),
            "filename": "audio.wav",
            "content_type": "audio/wav",
            "ciphertext_bytes": artifact_bytes,
            "plaintext_bytes": artifact_bytes,
            "decoded_pcm_bytes": 64 * 1024,
            "duration_ms": 1000,
            "key_epoch": job["key_epoch"],
        },
        "execution_plan": {
            "max_parallel_passes": 2,
            "pass_deadline_ms": 10_000,
            "passes": passes
            or [
                {
                    "pass_id": "pass-a",
                    "model_id": "model-a",
                    "model_revision": "revision-a",
                    "variant_id": "original",
                    "language": "de",
                }
            ],
        },
    }
    return SpeechReconciliationWorkerTask.from_mapping(payload)


def _staged(tmp_path: Path) -> StagedSpeechAudio:
    audio = DecodedPcmAudio(
        filename="audio.wav",
        pcm_s16le=b"\0\0" * 1600,
        sample_rate_hz=16_000,
        duration_ms=100,
        source_format="wav",
    )
    return StagedSpeechAudio(
        decoded=audio,
        encoded_path=tmp_path / "source.audio",
        wav_path=tmp_path / "decoded.wav",
        source_audio_digest=digest("audio"),
        workspace_root=tmp_path,
    )


def _model(model_id: str, backend: _Backend, *, revision: str | None = None, device: str = "cpu") -> LocalSpeechModel:
    return LocalSpeechModel(
        model_id=model_id,
        model_revision=revision or f"revision-{model_id[-1]}",
        manifest_digest=digest(f"manifest-{model_id}"),
        backend=backend,
        device=device,
    )


def test_allowlisted_ensemble_is_parallel_bounded_and_preserves_provenance(tmp_path: Path) -> None:
    tracker = _ConcurrencyTracker()
    left = _Backend("engine-a", "Guten Tag", tracker=tracker)
    right = _Backend("engine-b", "Guten Tag", confidence=0.8, tracker=tracker)
    task = _task(
        passes=[
            {
                "pass_id": "pass-a",
                "model_id": "model-a",
                "model_revision": "revision-a",
                "variant_id": "original",
                "language": "de",
            },
            {
                "pass_id": "pass-b",
                "model_id": "model-b",
                "model_revision": "revision-b",
                "variant_id": "original",
                "language": "de",
            },
        ]
    )
    ensemble = SpeechAsrEnsemble(
        models=LocalSpeechModelRegistry({"model-a": _model("model-a", left), "model-b": _model("model-b", right)})
    )

    result = ensemble.run(task, _staged(tmp_path))

    assert result.status == "completed"
    assert tracker.maximum == 2
    assert len(result.candidates) == 2
    for candidate in result.candidates:
        assert candidate.execution_location == "speech-reconciliation-worker"
        assert candidate.source_audio_digest == digest("audio")
        assert candidate.audio_variant_id == "original"
        assert candidate.model_revision in {"revision-a", "revision-b"}
        assert candidate.confidence in {0.8, 0.9}
        assert candidate.duration_ms == 100
        assert candidate.latency_ms is not None
        assert candidate.provenance["source_audio_digest"] == digest("audio")
        assert "untrusted" not in candidate.provenance


def test_partial_model_failure_retains_valid_candidate_with_safe_stable_status(tmp_path: Path) -> None:
    good = _Backend("engine-a", "Hallo Welt")
    bad = _Backend("engine-b", "", failure=True)
    task = _task(
        passes=[
            {
                "pass_id": "pass-a",
                "model_id": "model-a",
                "model_revision": "revision-a",
                "variant_id": "original",
                "language": "de",
            },
            {
                "pass_id": "pass-b",
                "model_id": "model-b",
                "model_revision": "revision-b",
                "variant_id": "original",
                "language": "de",
            },
        ]
    )
    result = SpeechAsrEnsemble(
        models=LocalSpeechModelRegistry({"model-a": _model("model-a", good), "model-b": _model("model-b", bad)})
    ).run(task, _staged(tmp_path))

    assert result.status == "partial"
    assert len(result.completed_pass_ids) == 1
    failed = next(item for item in result.candidates if item.status == "failed")
    assert failed.error is not None
    assert failed.error.message == failed.error.code
    assert "private model failure" not in str(failed.as_dict())


def test_model_variant_budget_and_cancel_checks_run_before_every_pass(tmp_path: Path) -> None:
    backend = _Backend("engine-a", "Hallo")
    model = _model("model-a", backend)
    unknown_model_task = _task(
        passes=[
            {
                "pass_id": "pass-x",
                "model_id": "model-x",
                "model_revision": "revision-x",
                "variant_id": "original",
                "language": None,
            }
        ]
    )
    ensemble = SpeechAsrEnsemble(models=LocalSpeechModelRegistry({"model-a": model}))
    with pytest.raises(SpeechAsrEnsembleError) as unknown:
        ensemble.run(unknown_model_task, _staged(tmp_path))
    assert unknown.value.reason_code == "speech_reconciliation_model_not_allowed"
    assert backend.calls == 0

    bad_variant = _task(
        passes=[
            {
                "pass_id": "pass-a",
                "model_id": "model-a",
                "model_revision": "revision-a",
                "variant_id": "network_download",
                "language": None,
            }
        ]
    )
    with pytest.raises(SpeechAsrEnsembleError) as variant:
        ensemble.run(bad_variant, _staged(tmp_path))
    assert variant.value.reason_code == "speech_reconciliation_variant_not_allowed"
    assert backend.calls == 0

    exhausted = ensemble.run(_task(cpu_time_ms=1), _staged(tmp_path))
    assert exhausted.status == "failed"
    assert exhausted.reason_code == "speech_reconciliation_cpu_budget_exhausted"
    assert backend.calls == 0

    def cancel() -> None:
        raise RuntimeError("cancel")

    cancelled = ensemble.run(_task(), _staged(tmp_path), cancellation_check=cancel)
    assert cancelled.status == "failed"
    assert cancelled.reason_code == "speech_reconciliation_cancelled"
    assert backend.calls == 0


def _peer(
    candidate_id: str,
    text: str,
    *,
    confidence: float = 0.9,
    quality_micros: int | None = None,
) -> PeerTranscriptCandidate:
    transcript = TranscriptionCandidate(
        candidate_id=candidate_id,
        backend="model-a",
        model="model-a",
        model_revision="revision-a",
        manifest_digest=digest("manifest-a"),
        text=text,
        confidence=confidence,
        status="succeeded",
        source_audio_digest=digest("audio"),
        lineage_id=candidate_id,
    )
    return PeerTranscriptCandidate(
        transcript=transcript,
        source_id=f"source-{candidate_id}",
        source_family=digest("audio"),
        contributor_digest=digest(f"contributor-{candidate_id}"),
        revision=1,
        lineage_digest=digest(f"lineage-{candidate_id}"),
        signature_digest=digest(f"signature-{candidate_id}"),
        authority_micros=800_000,
        quality_micros=(round(confidence * 1_000_000) if quality_micros is None else quality_micros),
    )


def test_resolver_uses_canonical_peer_and_fusion_ports_and_quarantines_conflicts() -> None:
    resolver = SpeechReconciliationResolver()
    resolved = resolver.resolve((_peer("candidate-a", "Guten Tag."), _peer("candidate-b", "Guten Tag!")))
    assert resolved.publishable is True
    assert resolved.transcript is not None
    assert resolved.transcript.provenance_valid is True
    source_tokens = {
        token for item in ("Guten Tag.", "Guten Tag!") for token in item.replace(".", " .").replace("!", " !").split()
    }
    assert set(resolved.transcript.text.replace(".", " .").replace("!", " !").split()) <= source_tokens

    unresolved = resolver.resolve((_peer("candidate-a", "Guten Tag"), _peer("candidate-b", "Falscher Wert")))
    assert unresolved.publishable is False
    assert unresolved.transcript is None
    assert unresolved.peer_resolution.unresolved_region_ids
    assert unresolved.unresolved_high_quality_conflict_count >= 1

    uncertain = resolver.resolve(
        (
            _peer("candidate-a", "Guten Tag", confidence=0.2),
            _peer("candidate-b", "Falscher Wert", confidence=0.2),
        )
    )
    assert uncertain.peer_resolution.unresolved_region_ids
    assert uncertain.unresolved_high_quality_conflict_count == 0


def test_resolver_blocks_an_injected_fusion_token_without_candidate_evidence() -> None:
    candidate = _peer("candidate-a", "bekannter Text")

    class _InventingFusion:
        def fuse(self, candidates):
            result = TranscriptionResult(
                text="erfundener Text",
                candidates=candidates,
                provenance_valid=True,
                decision_trace={
                    "token_provenance": (
                        {
                            "token": "erfundener",
                            "candidate_id": "candidate-a",
                            "source_token_index": 0,
                        },
                        {"token": "Text", "candidate_id": "candidate-a", "source_token_index": 1},
                    )
                },
            )
            return FusionOutcome(result=result, result_hash=digest("invented"))

    with pytest.raises(SpeechReconciliationResolutionError) as invented:
        SpeechReconciliationResolver(fusion=_InventingFusion()).resolve((candidate,))
    assert invented.value.reason_code == "speech_reconciliation_fusion_token_invented"


def test_checkpoint_resume_requires_exact_job_attempt_fence_consent_policy_ledger_and_key_binding(
    tmp_path: Path,
) -> None:
    task = _task(checkpoint_bytes=1024 * 1024)
    store = SpeechReconciliationCheckpointStore(
        tmp_path / "checkpoints",
        cipher=AesGcmSpeechCheckpointCipher(EpochKeyring({1: b"z" * 32}), nonce_factory=lambda size: b"n" * size),
    )
    state = b'{"stage":"slow_asr","completed_passes":["pass-a"]}'
    checkpoint = store.save(task, checkpoint_sequence=1, stage="slow_asr", state=state)
    loaded = store.resume(task, checkpoint, expected_stage="slow_asr")
    assert loaded.state == state

    for field, value in (
        ("attempt_id", "other-attempt"),
        ("fencing_epoch", 2),
        ("consent_version", 2),
        ("revocation_epoch", 1),
        ("input_manifest_digest", digest("other-manifest")),
        ("policy_digest", digest("other-policy")),
        ("ledger_sequence", 1),
        ("key_epoch", 2),
    ):
        changed = SpeechReconciliationCheckpoint.from_mapping({**checkpoint.to_dict(), field: value})
        with pytest.raises(SpeechCheckpointError) as mismatch:
            store.resume(task, changed, expected_stage="slow_asr")
        assert mismatch.value.reason_code == "speech_reconciliation_checkpoint_binding_mismatch"

    path = tmp_path / "checkpoints" / f"{checkpoint.checkpoint_digest}.checkpoint"
    sealed = path.read_bytes()
    path.write_bytes(sealed[:-1] + bytes([sealed[-1] ^ 1]))
    with pytest.raises(SpeechCheckpointError) as tamper:
        store.resume(task, checkpoint, expected_stage="slow_asr")
    assert tamper.value.reason_code == "speech_reconciliation_checkpoint_digest_mismatch"
