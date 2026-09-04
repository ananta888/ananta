from __future__ import annotations

from agent.services.dspy_program_artifact_store import DspyProgramArtifactStore
from tests.dspy_optimization.helpers import program


def test_artifact_registry_enforces_hold_promotion_and_tenant_read(tmp_path) -> None:
    store = DspyProgramArtifactStore(tmp_path / "artifacts")
    artifact = store.put(tenant_id="tenant-1", run_id="run-1", program=program())
    digest = artifact["digest"]
    assert store.get(tenant_id="tenant-1", run_id="run-1", digest=digest).digest == digest
    store.set_legal_hold(tenant_id="tenant-1", run_id="run-1", digest=digest, enabled=True)
    assert store.retention_sweep(now="9999-01-01T00:00:00Z")["count"] == 0
    store.set_legal_hold(tenant_id="tenant-1", run_id="run-1", digest=digest, enabled=False)
    store.bind_promotion(tenant_id="tenant-1", run_id="run-1", digest=digest, delta=1)
    assert store.retention_sweep(now="9999-01-01T00:00:00Z")["count"] == 0
    store.bind_promotion(tenant_id="tenant-1", run_id="run-1", digest=digest, delta=-1)
    assert store.retention_sweep(now="9999-01-01T00:00:00Z")["count"] == 1


def test_artifact_store_rejects_cross_tenant_and_symlink(tmp_path) -> None:
    store = DspyProgramArtifactStore(tmp_path / "artifacts")
    try:
        store.put(tenant_id="tenant-2", run_id="run-1", program=program())
    except PermissionError as exc:
        assert str(exc) == "dspy_artifact_tenant_mismatch"
    else:
        raise AssertionError("cross-tenant artifact must fail")
    tenant = tmp_path / "artifacts" / "tenant-1"
    tenant.symlink_to(tmp_path / "outside", target_is_directory=True)
    try:
        store.put(tenant_id="tenant-1", run_id="run-1", program=program())
    except ValueError as exc:
        assert str(exc) == "dspy_artifact_symlink_denied"
    else:
        raise AssertionError("symlinked tenant directory must fail")
