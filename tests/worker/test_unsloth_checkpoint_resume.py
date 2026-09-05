from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from worker.training.backends.base import (
    TrainingBackendError,
    TrainingContext,
    TrainingOutcome,
    run_backend,
)
from worker.training.backends.unsloth import UnslothTrainingBackend
from worker.training.backends.unsloth_audio import UnslothAudioTrainingBackend
from worker.training.backends.unsloth_checkpoint import (
    CHECKPOINT_MANIFEST_NAME,
    UnslothCheckpointLifecycle,
)
from worker.training.backends.unsloth_embedding import (
    UnslothEmbeddingTrainingBackend,
)
from worker.training.backends.unsloth_vision import UnslothVisionTrainingBackend
from worker.training.process_control import CancellationToken

_TENANT_HASH = "a" * 64
_DATASET_HASH = "b" * 64
_MODEL_HASH = "c" * 64
_CONFIG_HASH = "d" * 64


class _CheckpointBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.checkpoint_lifecycle = UnslothCheckpointLifecycle(backend_name=name)
        self.prepare_calls = 0

    def availability(self):
        return True, None

    def prepare(self, context: TrainingContext):
        self.prepare_calls += 1
        return {}

    def train(self, context: TrainingContext, _prepared: Any):
        checkpoint = context.checkpoint_root / "checkpoint-1"
        checkpoint.mkdir(parents=True, exist_ok=True)
        (checkpoint / "trainer_state.json").write_text(
            '{"step":1}',
            encoding="utf-8",
        )
        context.emit("progress", {"step": 1, "max_steps": 1})
        context.emit("checkpoint", {"step": 1, "name": checkpoint.name})
        return {"checkpoint": checkpoint}

    def evaluate(self, _context: TrainingContext, _prepared: Any, _trained: Any):
        return {"loss": 0.1}

    def save(
        self,
        context: TrainingContext,
        _prepared: Any,
        trained: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ):
        context.artifact_root.mkdir(parents=True, exist_ok=True)
        artifact = context.artifact_root / "adapter.safetensors"
        artifact.write_bytes(b"adapter")
        return TrainingOutcome(
            metrics=metrics,
            artifacts=(artifact,),
            best_checkpoint=trained["checkpoint"],
        )


def _request(
    *,
    backend: str,
    job_id: str,
    attempt_id: str,
    tenant_hash: str = _TENANT_HASH,
    dataset_hash: str = _DATASET_HASH,
    model_hash: str = _MODEL_HASH,
    resume_checkpoint: Any = None,
):
    return SimpleNamespace(
        backend=backend,
        job_id=job_id,
        attempt_id=attempt_id,
        fencing_token=1,
        tenant_scope_digest=tenant_hash,
        dataset=SimpleNamespace(identity_hash=dataset_hash),
        base_model=SimpleNamespace(snapshot_hash=model_hash),
        configuration=SimpleNamespace(identity_hash=_CONFIG_HASH),
        resume_checkpoint=resume_checkpoint,
    )


def _context(
    state: Path,
    *,
    request: Any,
    resume_path: Path | None = None,
):
    checkpoint_root = state / "jobs" / request.job_id / "attempts" / request.attempt_id / "checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    artifact_root = checkpoint_root.parent / "artifacts"
    events: list[tuple[str, Mapping[str, Any]]] = []
    context = TrainingContext(
        request=request,
        dataset=SimpleNamespace(
            dataset_hash=request.dataset.identity_hash,
            train_path=state / "train.jsonl",
            validation_path=state / "validation.jsonl",
            train_records=1,
            validation_records=1,
        ),
        model_path=state / "model",
        artifact_root=artifact_root,
        checkpoint_root=checkpoint_root,
        resume_path=resume_path,
        cancel=CancellationToken(),
        emit=lambda event_type, payload: events.append((event_type, dict(payload))),
        checkpoint_state_root=state,
    )
    return context, events


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(child.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(child.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _source_checkpoint(state: Path, backend_name: str):
    backend = _CheckpointBackend(backend_name)
    request = _request(
        backend=backend_name,
        job_id="job-source",
        attempt_id="attempt-source",
    )
    context, events = _context(state, request=request)
    outcome = run_backend(backend, context)
    checkpoint = outcome.best_checkpoint
    assert checkpoint is not None
    relative = checkpoint.relative_to(state).as_posix()
    binding = SimpleNamespace(
        job_id=request.job_id,
        source_attempt_id=request.attempt_id,
        base_model_hash=_MODEL_HASH,
        dataset_hash=_DATASET_HASH,
        configuration_hash=_CONFIG_HASH,
        checkpoint_sha256=_tree_hash(checkpoint),
    )
    return checkpoint, relative, binding, events


@pytest.mark.parametrize(
    "backend_type",
    [
        UnslothTrainingBackend,
        UnslothVisionTrainingBackend,
        UnslothAudioTrainingBackend,
        UnslothEmbeddingTrainingBackend,
    ],
)
def test_every_unsloth_backend_has_the_shared_checkpoint_lifecycle(backend_type):
    backend = backend_type()
    assert isinstance(backend.checkpoint_lifecycle, UnslothCheckpointLifecycle)


@pytest.mark.parametrize(
    "backend_name",
    ["unsloth", "unsloth_vision", "unsloth_audio", "unsloth_embedding"],
)
def test_checkpoint_manifest_is_atomic_bound_and_resume_admitted(
    tmp_path: Path,
    backend_name: str,
) -> None:
    checkpoint, relative, binding, events = _source_checkpoint(
        tmp_path,
        backend_name,
    )
    manifest_path = checkpoint / CHECKPOINT_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["tenant_scope_digest"] == _TENANT_HASH
    assert manifest["job_id"] == "job-source"
    assert manifest["attempt_id"] == "attempt-source"
    assert manifest["dataset_hash"] == _DATASET_HASH
    assert manifest["base_model_hash"] == _MODEL_HASH
    assert not list(checkpoint.glob(f".{CHECKPOINT_MANIFEST_NAME}.*.tmp"))
    assert [event_type for event_type, _payload in events] == [
        "progress",
        "checkpoint",
    ]

    resume = SimpleNamespace(relative_path=relative, binding=binding)
    target_request = _request(
        backend=backend_name,
        job_id="job-target",
        attempt_id="attempt-target",
        resume_checkpoint=resume,
    )
    target_context, _target_events = _context(
        tmp_path,
        request=target_request,
        resume_path=checkpoint,
    )
    target_backend = _CheckpointBackend(backend_name)
    run_backend(target_backend, target_context)
    assert target_backend.prepare_calls == 1


def test_latest_sealed_checkpoint_is_recovered_after_hard_restart(tmp_path: Path) -> None:
    checkpoint, relative, binding, _events = _source_checkpoint(tmp_path, "unsloth")
    request = _request(
        backend="unsloth",
        job_id="job-source",
        attempt_id="attempt-source",
    )
    lifecycle = UnslothCheckpointLifecycle(backend_name="unsloth")

    recovered = lifecycle.recover_latest(
        request=request,
        state_root=tmp_path,
        checkpoint_root=checkpoint.parent,
    )

    assert recovered == {
        "relative_path": relative,
        "binding": {
            "job_id": binding.job_id,
            "source_attempt_id": binding.source_attempt_id,
            "base_model_hash": binding.base_model_hash,
            "dataset_hash": binding.dataset_hash,
            "configuration_hash": binding.configuration_hash,
            "checkpoint_sha256": binding.checkpoint_sha256,
        },
    }


@pytest.mark.parametrize(
    "mismatch",
    ["tenant", "dataset", "base_model", "source_job", "source_attempt"],
)
def test_resume_fails_closed_before_prepare_on_binding_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    checkpoint, relative, binding, _events = _source_checkpoint(
        tmp_path,
        "unsloth",
    )
    tenant_hash = "e" * 64 if mismatch == "tenant" else _TENANT_HASH
    dataset_hash = "e" * 64 if mismatch == "dataset" else _DATASET_HASH
    model_hash = "e" * 64 if mismatch == "base_model" else _MODEL_HASH
    if mismatch == "source_job":
        binding.job_id = "wrong-job"
    if mismatch == "source_attempt":
        binding.source_attempt_id = "wrong-attempt"
    resume = SimpleNamespace(relative_path=relative, binding=binding)
    request = _request(
        backend="unsloth",
        job_id="job-target",
        attempt_id="attempt-target",
        tenant_hash=tenant_hash,
        dataset_hash=dataset_hash,
        model_hash=model_hash,
        resume_checkpoint=resume,
    )
    context, _events = _context(
        tmp_path,
        request=request,
        resume_path=checkpoint,
    )
    backend = _CheckpointBackend("unsloth")
    with pytest.raises(TrainingBackendError) as raised:
        run_backend(backend, context)
    assert raised.value.code == "checkpoint_binding_mismatch"
    assert backend.prepare_calls == 0


def test_resume_rejects_symlinked_checkpoint_tree_before_prepare(
    tmp_path: Path,
) -> None:
    checkpoint, relative, binding, _events = _source_checkpoint(
        tmp_path,
        "unsloth",
    )
    (checkpoint / "escaped").symlink_to(tmp_path / "outside")
    resume = SimpleNamespace(relative_path=relative, binding=binding)
    request = _request(
        backend="unsloth",
        job_id="job-target",
        attempt_id="attempt-target",
        resume_checkpoint=resume,
    )
    context, _events = _context(
        tmp_path,
        request=request,
        resume_path=checkpoint,
    )
    backend = _CheckpointBackend("unsloth")
    with pytest.raises(TrainingBackendError) as raised:
        run_backend(backend, context)
    assert raised.value.code == "checkpoint_symlink_forbidden"
    assert backend.prepare_calls == 0
