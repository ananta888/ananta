from __future__ import annotations

from dataclasses import replace

from agent.services.ml_intern_speech_adapter_registry import MlInternSpeechAdapterRegistry
from agent.services.ml_intern_speech_eval_service import MlInternSpeechEvalService
from tests.speech_adaptation_support import (
    AlwaysActiveAuthority,
    MemoryArtifactPort,
    SyntheticDatasetResolver,
    speech_job,
)
from voice_runtime.speech_adaptation import (
    LoadedSpeechAdapter,
    ReceiverLocalSpeechAdaptationRuntime,
    SpeechAdapterActivation,
)
from worker.speech_training.backend_registry import SpeechTrainingBackendRegistry
from worker.speech_training.backends import MockSpeechTrainingBackend
from worker.speech_training.evaluation import build_mock_evaluation
from worker.speech_training.result_publisher import SpeechResultPublisher
from worker.speech_training.runner import SpeechTrainingRunner


class _Loader:
    def __init__(self, *, fail_load: bool = False, fail_infer: bool = False, on_load=None) -> None:
        self.fail_load = fail_load
        self.fail_infer = fail_infer
        self.on_load = on_load
        self.unloaded: list[str] = []
        self.cleared: list[str] = []

    def load(self, *, artifact_ref, expected_sha256, base_model_id):
        del base_model_id
        if self.fail_load:
            raise RuntimeError("load failed")
        if self.on_load is not None:
            self.on_load()
        return LoadedSpeechAdapter(artifact_ref.rsplit("/", 1)[-1], expected_sha256, object())

    def infer(self, loaded, semantic_payload):
        del loaded
        if self.fail_infer:
            raise RuntimeError("infer failed")
        return b"adapted:" + semantic_payload

    def unload(self, loaded):
        self.unloaded.append(loaded.adapter_id)

    def clear_artifact_cache(self, artifact_sha256):
        self.cleared.append(artifact_sha256)


class _Base:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def reconstruct(self, semantic_payload):
        if self.fail:
            raise RuntimeError("base failed")
        return b"base:" + semantic_payload


def _trained_and_approved(tmp_path):
    job = speech_job()
    hub_port = MemoryArtifactPort()
    runner = SpeechTrainingRunner(
        registry=SpeechTrainingBackendRegistry([MockSpeechTrainingBackend()]),
        authority=AlwaysActiveAuthority(),
        dataset_resolver=SyntheticDatasetResolver(tmp_path / "dataset"),
        result_publisher=SpeechResultPublisher(hub_port, root=tmp_path),
        workspace_root=tmp_path,
        model_root=tmp_path / "models",
        clock_ms=lambda: 1_000_001,
    )
    result = runner.run(job)
    assert result.status == "completed" and result.artifact is not None
    report = build_mock_evaluation(job)
    report["hardware_profile"] = "synthetic-openvoice-v2-contract-test"
    decision = MlInternSpeechEvalService().decide(report, expected_bindings=report["bindings"])
    registry = MlInternSpeechAdapterRegistry(tmp_path / "registry.json", clock_ms=lambda: 1_000_000)
    record = registry.register_evaluated(
        adapter_id=result.artifact.artifact_id,
        version="v1",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id=job.scope.pair_id,
        direction=job.scope.direction,
        speaker_digest=job.scope.speaker_digest,
        scope_digest=job.scope.scope_digest,
        base_model_id=job.base_model.model_id,
        base_model_digest=job.base_model.model_digest,
        backend=job.configuration.backend,
        backend_digest=job.configuration.backend_digest,
        dataset_digest=job.dataset.dataset_digest,
        split_digest=job.dataset.split_digest,
        evaluation=decision,
        consent_digest=job.consent.consent_digest,
        consent_expires_at_ms=1_200_000,
        artifact_ref=result.artifact.artifact_ref,
        artifact_sha256=result.artifact.sha256,
        artifact_size_bytes=result.artifact.size_bytes,
        expires_at_ms=1_100_000,
    )
    record = registry.approve(
        record.adapter_id,
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id=record.pair_id,
        direction=record.direction,
        expected_version=record.registry_version,
        authorized_confirmation=True,
        approved_by="admin-test",
        reason_code="manual_quality_approval",
        current_consent_digest=record.consent_digest,
    )
    activation = SpeechAdapterActivation(
        adapter_id=record.adapter_id,
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id=record.pair_id,
        direction=record.direction,
        speaker_digest=record.speaker_digest,
        base_model_id=record.base_model_id,
        base_model_digest=record.base_model_digest,
        consent_digest=record.consent_digest,
    )
    return registry, record, activation


def test_mock_train_evaluate_approve_load_infer_expire_and_revoke(tmp_path) -> None:
    registry, record, activation = _trained_and_approved(tmp_path)
    now = [1_000_000]
    loader = _Loader()
    runtime = ReceiverLocalSpeechAdaptationRuntime(
        registry=registry,
        loader=loader,
        base=_Base(),
        clock_ms=lambda: now[0],
    )
    adapted = runtime.infer(activation, b"semantic")
    assert adapted.mode == "adapted"
    assert adapted.audio == b"adapted:semantic"

    now[0] = record.expires_at_ms
    expired = runtime.infer(activation, b"semantic")
    assert expired.mode == "base"
    assert expired.reason_code == "speech_adapter_expired"
    assert record.artifact_sha256 in loader.cleared

    now[0] = 1_000_000
    runtime.activate(activation)
    revoked = registry.change_status(
        record.adapter_id,
        target="revoked",
        tenant_id="tenant-test",
        owner_subject="owner-test",
        pair_id=record.pair_id,
        direction=record.direction,
        expected_version=record.registry_version,
        actor="admin-test",
        reason_code="consent_revoked",
    )
    assert revoked.status == "revoked"
    fallback = runtime.infer(activation, b"semantic")
    assert fallback.mode == "base"
    assert fallback.reason_code == "speech_adapter_not_approved"


def test_load_or_base_failure_cleans_cache_and_uses_ordinary_audio(tmp_path) -> None:
    registry, record, activation = _trained_and_approved(tmp_path)
    loader = _Loader(fail_load=True)
    runtime = ReceiverLocalSpeechAdaptationRuntime(
        registry=registry,
        loader=loader,
        base=_Base(fail=True),
        clock_ms=lambda: 1_000_000,
    )
    result = runtime.infer(activation, b"semantic", ordinary_audio=b"ordinary")
    assert result.mode == "ordinary_audio"
    assert result.audio == b"ordinary"
    assert result.reason_code == "speech_adapter_load_failed"
    assert record.artifact_sha256 in loader.cleared


def test_revoke_during_load_is_revalidated_before_first_inference(tmp_path) -> None:
    registry, record, activation = _trained_and_approved(tmp_path)

    def revoke_during_load() -> None:
        registry.change_status(
            record.adapter_id,
            target="revoked",
            tenant_id="tenant-test",
            owner_subject="owner-test",
            pair_id=record.pair_id,
            direction=record.direction,
            expected_version=record.registry_version,
            actor="authority-race-test",
            reason_code="consent_revoked_during_load",
        )

    loader = _Loader(on_load=revoke_during_load)
    runtime = ReceiverLocalSpeechAdaptationRuntime(
        registry=registry,
        loader=loader,
        base=_Base(),
        clock_ms=lambda: 1_000_000,
    )

    result = runtime.infer(activation, b"semantic")

    assert result.mode == "base"
    assert result.reason_code == "speech_adapter_not_approved"
    assert loader.unloaded == [record.adapter_id]
    assert loader.cleared == [record.artifact_sha256]


def test_rollback_version_change_during_load_cannot_publish_stale_handle(tmp_path) -> None:
    _registry, record, activation = _trained_and_approved(tmp_path)

    class MutableRegistry:
        current = record

        def get_for_pair(self, adapter_id, *, tenant_id, owner_subject, pair_id, direction):
            assert (adapter_id, tenant_id, owner_subject, pair_id, direction) == (
                record.adapter_id,
                "tenant-test",
                "owner-test",
                record.pair_id,
                record.direction,
            )
            return self.current

    registry = MutableRegistry()

    def rollback_during_load() -> None:
        registry.current = replace(
            record,
            registry_version=record.registry_version + 1,
            rollback_of_adapter_id="speech-adapter-superseded",
            updated_at_ms=record.updated_at_ms + 1,
        )

    loader = _Loader(on_load=rollback_during_load)
    runtime = ReceiverLocalSpeechAdaptationRuntime(
        registry=registry,
        loader=loader,
        base=_Base(),
        clock_ms=lambda: 1_000_000,
    )

    result = runtime.infer(activation, b"semantic")

    assert result.mode == "base"
    assert result.reason_code == "speech_adapter_authority_changed"
    assert loader.unloaded == [record.adapter_id]
    assert loader.cleared == [record.artifact_sha256]
