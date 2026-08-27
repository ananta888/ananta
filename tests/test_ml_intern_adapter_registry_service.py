"""Tests fuer ml_intern_adapter_registry_service (MLLORA-006/016)."""

import hashlib
import json
import multiprocessing
import time
from pathlib import Path

import pytest

from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    RegistryError,
    RegistryNotFoundError,
    RegistryVersionConflict,
    make_config_hash,
)


def _svc(tmp_path: Path) -> MlInternAdapterRegistryService:
    return MlInternAdapterRegistryService(tmp_path / "adapter_registry.json")


def _register(svc, adapter_id="test-adapter-v1", base_model="qwen2.5-coder-7b"):
    return svc.register(
        adapter_id=adapter_id,
        display_name="Test Adapter",
        version="1.0",
        base_model=base_model,
        method="qlora",
        task_kinds=["todo_json_generation"],
    )


def _register_trained(svc, **overrides):
    values = {
        "adapter_id": "imported-adapter-v1",
        "display_name": "Imported Adapter",
        "version": "1.0",
        "base_model": "qwen2.5-coder-7b",
        "method": "lora",
        "artifact_paths": {"adapter_dir": "/verified/adapter"},
        "config_hash": "a" * 64,
        "artifact_sha256": "b" * 64,
    }
    values.update(overrides)
    return svc.register_trained(**values)


class _SlowLoadRegistry(MlInternAdapterRegistryService):
    def _load(self):
        records = super()._load()
        time.sleep(0.15)
        return records


def _register_trained_in_process(registry_path, adapter_id, barrier, results):
    try:
        service = _SlowLoadRegistry(registry_path)
        barrier.wait(timeout=10)
        service.register_trained(
            adapter_id=adapter_id,
            display_name=adapter_id,
            version="1.0",
            base_model="local/base",
            method="lora",
            artifact_paths={"adapter_dir": f"/verified/{adapter_id}"},
            config_hash="a" * 64,
            artifact_sha256=hashlib.sha256(adapter_id.encode()).hexdigest(),
        )
        results.put(None)
    except Exception as exc:  # pragma: no cover - reported in the parent process
        results.put(repr(exc))


def test_register_and_get(tmp_path):
    svc = _svc(tmp_path)
    record = _register(svc)
    assert record.adapter_id == "test-adapter-v1"
    assert record.status == "created"
    fetched = svc.get("test-adapter-v1")
    assert fetched is not None
    assert fetched.base_model == "qwen2.5-coder-7b"


def test_local_release_target_is_immutable_lineage(tmp_path):
    svc = _svc(tmp_path)
    created = _register_trained(svc, release_target="needle2")
    assert created.release_target == "needle2"
    assert svc.get(created.adapter_id).release_target == "needle2"

    with pytest.raises(RegistryError, match="release_target"):
        _register_trained(svc, release_target=None)

    with pytest.raises(RegistryError, match="release_target"):
        _register_trained(
            svc,
            adapter_id="invalid-target",
            release_target="worker-controlled",
        )


def test_governed_local_promotion_is_atomic_provenance_bound_and_replayable(tmp_path):
    svc = _svc(tmp_path)
    _register_trained(
        svc,
        release_target="needle2",
        dataset_hash="c" * 64,
        source_ids=["SRC_approved:1"],
        run_ids=["RUN_approved:1"],
        provenance_verified=True,
        tenant_id="tenant",
        owner_subject="owner",
    )
    evaluated = svc.set_eval_report(
        "imported-adapter-v1",
        eval_report_ref="evaluation-1",
        eval_score=1.0,
        tenant_id="tenant",
        owner_subject="owner",
    )

    first, replayed = svc.promote_local_evaluated(
        "imported-adapter-v1",
        lifecycle_evidence_sha256="d" * 64,
        approved_by="hub-policy",
        idempotency_key="local-promotion-1",
        tenant_id="tenant",
        owner_subject="owner",
        expected_version=evaluated.registry_version,
        minimum_eval_score=1.0,
    )
    replay, was_replayed = svc.promote_local_evaluated(
        "imported-adapter-v1",
        lifecycle_evidence_sha256="d" * 64,
        approved_by="hub-policy",
        idempotency_key="local-promotion-1",
        tenant_id="tenant",
        owner_subject="owner",
        expected_version=evaluated.registry_version,
        minimum_eval_score=1.0,
    )

    assert replayed is False
    assert was_replayed is True
    assert first == replay
    assert first.status == "approved"
    assert first.promotion_history[-1]["lifecycle_evidence_sha256"] == "d" * 64


def test_governed_local_promotion_rejects_unverified_provenance(tmp_path):
    svc = _svc(tmp_path)
    _register_trained(
        svc,
        release_target="needle2",
        tenant_id="tenant",
        owner_subject="owner",
    )
    evaluated = svc.set_eval_report(
        "imported-adapter-v1",
        eval_report_ref="evaluation-1",
        eval_score=1.0,
        tenant_id="tenant",
        owner_subject="owner",
    )

    with pytest.raises(RegistryError, match="provenance is unverified"):
        svc.promote_local_evaluated(
            "imported-adapter-v1",
            lifecycle_evidence_sha256="d" * 64,
            approved_by="hub-policy",
            idempotency_key="local-promotion-1",
            tenant_id="tenant",
            owner_subject="owner",
            expected_version=evaluated.registry_version,
            minimum_eval_score=1.0,
        )


def test_duplicate_register_raises(tmp_path):
    svc = _svc(tmp_path)
    _register(svc)
    with pytest.raises(RegistryError, match="already exists"):
        _register(svc)


def test_valid_status_transitions(tmp_path):
    svc = _svc(tmp_path)
    _register(svc)
    svc.transition("test-adapter-v1", "training")
    svc.transition("test-adapter-v1", "trained")
    assert svc.get("test-adapter-v1").status == "trained"


def test_invalid_transition_blocked(tmp_path):
    svc = _svc(tmp_path)
    _register(svc)
    with pytest.raises(RegistryError, match="invalid transition"):
        svc.transition("test-adapter-v1", "approved")  # created -> approved nicht erlaubt


def test_created_to_approved_without_eval_blocked(tmp_path):
    """Test: created -> approved ohne Eval wird blockiert."""
    svc = _svc(tmp_path)
    _register(svc)
    svc.transition("test-adapter-v1", "training")
    svc.transition("test-adapter-v1", "trained")
    # trained -> approved ohne eval_report_ref blockiert
    with pytest.raises(RegistryError):
        svc.approve("test-adapter-v1", approved_by="peter", reason="test", require_eval_report=True)


def test_approve_after_eval(tmp_path):
    svc = _svc(tmp_path)
    _register(svc)
    svc.transition("test-adapter-v1", "training")
    svc.transition("test-adapter-v1", "trained")
    svc.set_eval_report("test-adapter-v1", eval_report_ref="artifacts/lora/eval.json", eval_score=0.85)
    record = svc.approve("test-adapter-v1", approved_by="peter", reason="good eval")
    assert record.status == "approved"
    assert record.approved_by == "peter"
    assert record.eval_report_ref == "artifacts/lora/eval.json"


def test_reject(tmp_path):
    svc = _svc(tmp_path)
    _register(svc)
    svc.transition("test-adapter-v1", "training")
    svc.transition("test-adapter-v1", "trained")
    svc.set_eval_report("test-adapter-v1", eval_report_ref="artifacts/lora/eval.json", eval_score=0.1)
    record = svc.reject("test-adapter-v1", reason="adapter worse than base")
    assert record.status == "rejected"
    assert "worse" in record.rejected_reason


def test_deprecated_adapter_not_active_default(tmp_path):
    svc = _svc(tmp_path)
    _register(svc)
    svc.transition("test-adapter-v1", "training")
    svc.transition("test-adapter-v1", "trained")
    svc.set_eval_report("test-adapter-v1", eval_report_ref="x", eval_score=0.9)
    svc.approve("test-adapter-v1", approved_by="peter", reason="ok")
    svc.deprecate("test-adapter-v1")
    # deprecated Adapter darf nicht als active default aufloesbar sein
    result = svc.resolve_active_adapter(base_model="qwen2.5-coder-7b", approved_only=True)
    assert result is None


def test_base_model_mismatch_blocked(tmp_path):
    svc = _svc(tmp_path)
    _register(svc, base_model="qwen2.5-coder-7b")
    result = svc.resolve_active_adapter(base_model="llama-3-8b", approved_only=True)
    assert result is None


def test_to_read_model_no_sensitive_paths(tmp_path):
    svc = _svc(tmp_path)
    _register(svc, adapter_id="a1")
    data = svc.to_read_model()
    assert data["count"] == 1
    assert data["approved_count"] == 0
    # Keine safetensors-Pfade in der lesbaren Ausgabe
    item = data["items"][0]
    assert "artifact_paths" not in item


def test_auto_activate_adapter_never_default(tmp_path):
    """auto_activate_adapter muss per Config-Default false sein."""
    from agent.services.ml_intern_training_config_service import normalize_ml_intern_training_config

    cfg = normalize_ml_intern_training_config({})
    assert cfg["auto_activate_adapter"] is False
    cfg2 = normalize_ml_intern_training_config({"auto_activate_adapter": True})
    assert cfg2["auto_activate_adapter"] is True  # Kann gesetzt werden, aber Default ist false


def test_registry_missing_returns_empty_list(tmp_path):
    svc = _svc(tmp_path)  # Datei existiert noch nicht
    result = svc.list_adapters()
    assert result == []


def test_make_config_hash_stable():
    h1 = make_config_hash({"a": 1, "b": 2})
    h2 = make_config_hash({"b": 2, "a": 1})
    assert h1 == h2  # Sortiert -> stabiler Hash


def test_registry_persisted_as_valid_json(tmp_path):
    svc = _svc(tmp_path)
    _register(svc)
    reg_path = tmp_path / "adapter_registry.json"
    assert reg_path.exists()
    data = json.loads(reg_path.read_text())
    assert data["schema"] == "mlintern_adapter_registry.v2"
    assert len(data["adapters"]) == 1


def test_register_trained_atomically_creates_and_idempotently_replays(tmp_path):
    svc = _svc(tmp_path)

    created = _register_trained(svc)
    replayed = _register_trained(svc)

    assert created.status == "trained"
    assert replayed == created
    assert len(svc.list_adapters()) == 1


def test_register_trained_is_atomic_across_hub_processes(tmp_path):
    context = multiprocessing.get_context("fork")
    registry_path = tmp_path / "adapter_registry.json"
    barrier = context.Barrier(3)
    results = context.Queue()
    processes = [
        context.Process(
            target=_register_trained_in_process,
            args=(registry_path, adapter_id, barrier, results),
        )
        for adapter_id in ("parallel-adapter-a", "parallel-adapter-b")
    ]
    for process in processes:
        process.start()
    barrier.wait(timeout=10)
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert [results.get(timeout=2) for _process in processes] == [None, None]
    persisted = MlInternAdapterRegistryService(registry_path).list_adapters()
    assert {record.adapter_id for record in persisted} == {
        "parallel-adapter-a",
        "parallel-adapter-b",
    }


@pytest.mark.parametrize(
    ("field", "different"),
    [
        ("version", "2.0"),
        ("base_model", "different/base"),
        ("method", "qlora"),
        ("config_hash", "c" * 64),
        ("artifact_sha256", "d" * 64),
    ],
)
def test_register_trained_rejects_any_immutable_binding_mismatch(tmp_path, field, different):
    svc = _svc(tmp_path)
    _register_trained(svc)

    with pytest.raises(RegistryError, match=field):
        _register_trained(svc, **{field: different})

    assert len(svc.list_adapters()) == 1
    assert svc.get("imported-adapter-v1").status == "trained"


def test_register_trained_save_failure_leaves_no_new_domain_record(tmp_path, monkeypatch):
    svc = _svc(tmp_path)

    def fail_save(_records):
        raise OSError("simulated atomic save failure")

    monkeypatch.setattr(svc, "_save", fail_save)
    with pytest.raises(OSError, match="simulated atomic"):
        _register_trained(svc)

    assert svc.get("imported-adapter-v1") is None


def test_register_trained_resumes_created_record_in_one_atomic_write(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    svc.register(
        adapter_id="imported-adapter-v1",
        display_name="Imported Adapter",
        version="1.0",
        base_model="qwen2.5-coder-7b",
        method="lora",
        artifact_paths={"adapter_dir": "/interrupted/adapter"},
        config_hash="a" * 64,
        artifact_sha256="b" * 64,
    )
    original_save = svc._save
    writes = 0

    def count_save(records):
        nonlocal writes
        writes += 1
        original_save(records)

    monkeypatch.setattr(svc, "_save", count_save)
    resumed = _register_trained(svc)

    assert writes == 1
    assert resumed.status == "trained"
    assert resumed.artifact_paths == {"adapter_dir": "/verified/adapter"}


def test_register_trained_resume_failure_preserves_previous_domain_record(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    svc.register(
        adapter_id="imported-adapter-v1",
        display_name="Imported Adapter",
        version="1.0",
        base_model="qwen2.5-coder-7b",
        method="lora",
        artifact_paths={"adapter_dir": "/interrupted/adapter"},
        config_hash="a" * 64,
        artifact_sha256="b" * 64,
    )

    def fail_save(_records):
        raise OSError("simulated resume save failure")

    monkeypatch.setattr(svc, "_save", fail_save)
    with pytest.raises(OSError, match="simulated resume"):
        _register_trained(svc)

    persisted = svc.get("imported-adapter-v1")
    assert persisted.status == "created"
    assert persisted.artifact_paths == {"adapter_dir": "/interrupted/adapter"}


def test_registry_enforces_exact_tenant_and_owner_scope_for_all_reads_and_actions(tmp_path):
    svc = _svc(tmp_path)
    tenant_a = {"tenant_id": "tenant-a", "owner_subject": "alice"}
    tenant_b = {"tenant_id": "tenant-b", "owner_subject": "alice"}
    other_owner = {"tenant_id": "tenant-a", "owner_subject": "bob"}

    first = svc.register(
        adapter_id="shared-id",
        display_name="Tenant A adapter",
        version="artifact-1",
        base_model="base-local",
        **tenant_a,
    )
    second = svc.register(
        adapter_id="shared-id",
        display_name="Tenant B adapter",
        version="artifact-2",
        base_model="base-local",
        **tenant_b,
    )

    assert svc.get("shared-id", **tenant_a) == first
    assert svc.get("shared-id", **tenant_b) == second
    assert svc.get("shared-id", **other_owner) is None
    assert svc.list_adapters(**other_owner) == []
    assert svc.list_adapters() == []
    tenant_a_digest = hashlib.sha256(b"ananta.ml-intern-training.scope.v1\x00tenant-a\x00alice").hexdigest()
    tenant_b_digest = hashlib.sha256(b"ananta.ml-intern-training.scope.v1\x00tenant-b\x00alice").hexdigest()
    assert svc.get_by_scope_digest("shared-id", tenant_a_digest) == first
    assert svc.get_by_scope_digest("shared-id", tenant_b_digest) == second
    with pytest.raises(RegistryNotFoundError):
        svc.transition("shared-id", "training", **other_owner)

    assert svc.get("shared-id", **tenant_a).status == "created"
    assert svc.get("shared-id", **tenant_b).status == "created"


def test_registry_version_is_monotone_and_stale_cas_has_no_side_effect(tmp_path):
    svc = _svc(tmp_path)
    scope = {"tenant_id": "tenant-a", "owner_subject": "alice"}
    created = svc.register(
        adapter_id="versioned",
        display_name="Versioned adapter",
        version="artifact-v7",
        base_model="base-local",
        **scope,
    )
    assert created.registry_version == 1

    training = svc.transition(
        "versioned",
        "training",
        expected_version=1,
        **scope,
    )
    assert training.registry_version == 2
    assert training.version == "artifact-v7"

    with pytest.raises(RegistryVersionConflict, match="current 2"):
        svc.transition(
            "versioned",
            "trained",
            expected_version=1,
            **scope,
        )

    persisted = svc.get("versioned", **scope)
    assert persisted.status == "training"
    assert persisted.registry_version == 2


def test_legacy_v1_records_remain_readable_only_in_explicit_legacy_scope(tmp_path):
    registry_path = tmp_path / "adapter_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema": "mlintern_adapter_registry.v1",
                "adapters": [
                    {
                        "adapter_id": "legacy-adapter",
                        "display_name": "Legacy adapter",
                        "version": "1.0",
                        "base_model": "base-local",
                        "method": "lora",
                        "status": "created",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    svc = MlInternAdapterRegistryService(registry_path)

    legacy = svc.get("legacy-adapter")
    assert legacy is not None
    assert legacy.registry_version == 1
    assert (
        svc.get(
            "legacy-adapter",
            tenant_id="tenant-a",
            owner_subject="alice",
        )
        is None
    )

    migrated = svc.transition("legacy-adapter", "training", expected_version=1)
    assert migrated.registry_version == 2
    assert json.loads(registry_path.read_text(encoding="utf-8"))["schema"] == ("mlintern_adapter_registry.v2")


def test_register_trained_persists_and_fences_canonical_provenance(tmp_path):
    svc = _svc(tmp_path)
    binding = {
        "dataset_hash": "c" * 64,
        "source_ids": ["SRC_training-corpus"],
        "run_ids": ["RUN_materialization-1"],
        "provenance_verified": True,
    }
    created = _register_trained(svc, **binding)

    assert created.dataset_hash == "c" * 64
    assert created.source_ids == ["SRC_training-corpus"]
    assert created.run_ids == ["RUN_materialization-1"]
    assert created.provenance_verified is True
    with pytest.raises(RegistryError, match="dataset_hash"):
        _register_trained(svc, **{**binding, "dataset_hash": "d" * 64})


def test_registry_never_marks_incomplete_provenance_verified(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(RegistryError, match="verified provenance requires"):
        _register_trained(
            svc,
            dataset_hash="c" * 64,
            source_ids=["SRC_training-corpus"],
            provenance_verified=True,
        )
