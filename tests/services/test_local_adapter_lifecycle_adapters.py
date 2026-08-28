from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.local_adapter_lifecycle_adapters import (
    LocalModelRuntimeRestartAdapter,
    MlInternLocalAdapterCandidateSourcePort,
    MlInternLocalAdapterRegistryPort,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal


class _Registry:
    def __init__(self, record):
        self.record = record
        self.rollback_calls = []
        self.promotion_calls = []

    def get(self, *_args, **_kwargs):
        return self.record

    def rollback(self, *args, **kwargs):
        self.rollback_calls.append((args, kwargs))
        deprecated = SimpleNamespace(registry_version=8)
        target = SimpleNamespace(adapter_id="previous-adapter", artifact_sha256="c" * 64)
        return deprecated, target

    def promote_local_evaluated(self, *args, **kwargs):
        self.promotion_calls.append((args, kwargs))
        return SimpleNamespace(status="approved", registry_version=7), False


def test_registry_adapter_uses_existing_atomic_promotion_and_scoped_rollback() -> None:
    record = SimpleNamespace(adapter_id="candidate-1", status="evaluated", registry_version=6)
    registry = _Registry(record)
    principal = MlInternTrainingPrincipal(tenant_id="tenant-1", subject="user-1")
    adapter = MlInternLocalAdapterRegistryPort(
        registry=registry,
        principal=principal,
        approved_by="hub-policy",
        minimum_score=0.9,
    )

    result = adapter.promote(
        candidate_id="candidate-1",
        expected_revision=6,
        idempotency_key="promotion-1",
        evidence_sha256="a" * 64,
    )
    rollback = adapter.rollback(candidate_id="candidate-1", reason_code="live_schema_error")

    assert result == {"registry_revision": 7, "replayed": False}
    assert registry.promotion_calls[0][1]["expected_version"] == 6
    assert registry.promotion_calls[0][1]["lifecycle_evidence_sha256"] == "a" * 64
    assert registry.rollback_calls[0][1]["tenant_id"] == "tenant-1"
    assert rollback["rollback_target_id"] == "previous-adapter"
    assert rollback["rollback_target_sha256"] == "c" * 64


class _Lifecycle:
    def __init__(self):
        self.applied = []

    def evaluate(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(admitted=True, decision_id="decision-1")

    def apply(self, **kwargs):
        self.applied.append(kwargs)
        return SimpleNamespace(status="completed")


class _Activation:
    def __init__(self):
        self.switches = []
        self.restores = []

    def switch(self, **values):
        self.switches.append(values)
        return "previous"

    def restore(self, **values):
        self.restores.append(values)


def test_runtime_adapter_binds_candidate_hash_to_admitted_restart_request() -> None:
    lifecycle = _Lifecycle()
    activation = _Activation()
    adapter = LocalModelRuntimeRestartAdapter(lifecycle=lifecycle, capabilities=(), activation=activation)

    assert adapter.restart(target="needle2", candidate_id="candidate-1", candidate_sha256="b" * 64) is True
    assert lifecycle.request["request_id"] == f"adapter-restart-needle2-{'b' * 64}"
    assert lifecycle.applied == [{"decision_id": "decision-1", "action": "restart"}]
    assert activation.switches == [
        {"target": "needle2", "candidate_id": "candidate-1", "candidate_sha256": "b" * 64}
    ]


def test_runtime_adapter_restores_projection_when_restart_fails() -> None:
    lifecycle = _Lifecycle()
    lifecycle.apply = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("restart failed"))
    activation = _Activation()
    adapter = LocalModelRuntimeRestartAdapter(lifecycle=lifecycle, capabilities=(), activation=activation)

    with pytest.raises(RuntimeError, match="restart failed"):
        adapter.restart(target="needle2", candidate_id="candidate-1", candidate_sha256="b" * 64)

    assert activation.restores == [{"target": "needle2", "previous": "previous"}]


def test_registry_candidate_source_requires_approved_exact_binding(tmp_path) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    record = SimpleNamespace(
        adapter_id="candidate-1",
        status="approved",
        release_target="needle2",
        artifact_sha256="b" * 64,
        artifact_paths={"adapter_dir": str(candidate)},
    )
    adapter = MlInternLocalAdapterCandidateSourcePort(
        registry=_Registry(record),
        principal=MlInternTrainingPrincipal(tenant_id="tenant-1", subject="user-1"),
    )

    resolved = adapter.resolve(candidate_id="candidate-1", target="needle2", candidate_sha256="b" * 64)
    assert resolved.artifact_directory == candidate

    record.status = "evaluated"
    with pytest.raises(ValueError, match="candidate_unverified"):
        adapter.resolve(candidate_id="candidate-1", target="needle2", candidate_sha256="b" * 64)
