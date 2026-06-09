"""Tests fuer ml_intern_adapter_registry_service (MLLORA-006/016)."""

import json
import pytest
from pathlib import Path

from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    RegistryError,
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


def test_register_and_get(tmp_path):
    svc = _svc(tmp_path)
    record = _register(svc)
    assert record.adapter_id == "test-adapter-v1"
    assert record.status == "created"
    fetched = svc.get("test-adapter-v1")
    assert fetched is not None
    assert fetched.base_model == "qwen2.5-coder-7b"


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
    assert data["schema"] == "mlintern_adapter_registry.v1"
    assert len(data["adapters"]) == 1
