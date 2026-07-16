from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from agent.services.ml_intern_adapter_export_service import AdapterExportError, MlInternAdapterExportService
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService


def _safetensors_bytes(value: bytes = b"\x00\x00\x00\x00") -> bytes:
    header = json.dumps(
        {"lora.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, len(value)]}},
        separators=(",", ":"),
    ).encode("utf-8")
    return len(header).to_bytes(8, "little") + header + value


def _evaluated_adapter(
    tmp_path: Path,
    *,
    scope: dict[str, str] | None = None,
) -> tuple[MlInternAdapterExportService, Path, MlInternAdapterRegistryService]:
    artifact_root = tmp_path / "artifacts"
    adapter_dir = artifact_root / "adapters" / "safe-adapter-v1"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": "local/base", "peft_type": "LORA"}),
        encoding="utf-8",
    )
    (adapter_dir / "adapter_model.safetensors").write_bytes(_safetensors_bytes())
    inspected = MlInternArtifactSecurityService(storage_root=artifact_root).validate_adapter_tree(adapter_dir)

    registry = MlInternAdapterRegistryService(artifact_root / "adapter_registry.json")
    ownership = dict(scope or {})
    registry.register(
        adapter_id="safe-adapter-v1",
        display_name="Safe adapter",
        version="1",
        base_model="local/base",
        method="lora",
        artifact_paths={"adapter_dir": str(adapter_dir)},
        artifact_sha256=inspected["tree_sha256"],
        dataset_hash="a" * 64,
        config_hash="b" * 64,
        **ownership,
    )
    registry.transition("safe-adapter-v1", "training", **ownership)
    registry.transition("safe-adapter-v1", "trained", **ownership)
    registry.set_eval_report(
        "safe-adapter-v1",
        eval_report_ref="eval-job-1",
        eval_score=0.25,
        **ownership,
    )
    return MlInternAdapterExportService(artifact_root=artifact_root, registry=registry), adapter_dir, registry


def test_export_is_deterministic_hash_bound_and_contains_only_verified_files(tmp_path: Path) -> None:
    service, _adapter_dir, _registry = _evaluated_adapter(tmp_path)

    first = service.export("safe-adapter-v1")
    second = service.export("safe-adapter-v1")

    assert first == second
    archive_path, digest = service.resolve_export(first["artifact_id"])
    assert digest == first["sha256"]
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            "adapter_config.json",
            "adapter_model.safetensors",
            "ananta_export_manifest.json",
        ]
        manifest = json.loads(archive.read("ananta_export_manifest.json"))
        assert manifest["artifact_sha256"]
        assert manifest["adapter_id"] == "safe-adapter-v1"
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_export_rejects_post_registration_artifact_tampering(tmp_path: Path) -> None:
    service, adapter_dir, _registry = _evaluated_adapter(tmp_path)
    (adapter_dir / "adapter_model.safetensors").write_bytes(_safetensors_bytes(b"\x01\x00\x00\x00"))

    with pytest.raises(AdapterExportError) as error:
        service.export("safe-adapter-v1")

    assert error.value.reason_code == "adapter_hash_mismatch"


def test_existing_export_tampering_is_regenerated_and_direct_download_fails_closed(tmp_path: Path) -> None:
    service, _adapter_dir, _registry = _evaluated_adapter(tmp_path)
    first = service.export("safe-adapter-v1")
    archive_path, _digest = service.resolve_export(first["artifact_id"])
    archive_path.write_bytes(b"not-a-verified-adapter-export")

    with pytest.raises(AdapterExportError) as tampered:
        service.resolve_export(first["artifact_id"])
    assert tampered.value.reason_code == "export_hash_mismatch"

    regenerated = service.export("safe-adapter-v1")
    assert regenerated == first
    resolved, digest = service.resolve_export(first["artifact_id"])
    assert digest == first["sha256"]
    with zipfile.ZipFile(resolved) as archive:
        assert "ananta_export_manifest.json" in archive.namelist()


def test_export_rejects_legacy_registry_record_without_hash_binding(tmp_path: Path) -> None:
    service, _adapter_dir, _registry = _evaluated_adapter(tmp_path)
    registry_path = tmp_path / "artifacts" / "adapter_registry.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["adapters"][0].pop("artifact_sha256")
    registry_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AdapterExportError) as error:
        service.export("safe-adapter-v1")

    assert error.value.reason_code == "adapter_hash_unbound"


def test_scoped_export_cannot_be_read_or_recreated_by_another_owner(tmp_path: Path) -> None:
    owner = {"tenant_id": "tenant-a", "owner_subject": "alice"}
    service, _adapter_dir, _registry = _evaluated_adapter(tmp_path, scope=owner)
    exported = service.export("safe-adapter-v1", **owner)

    with pytest.raises(AdapterExportError) as hidden_record:
        service.export(
            "safe-adapter-v1",
            tenant_id="tenant-a",
            owner_subject="bob",
        )
    with pytest.raises(AdapterExportError) as hidden_export:
        service.resolve_export(
            exported["artifact_id"],
            tenant_id="tenant-a",
            owner_subject="bob",
        )

    assert hidden_record.value.reason_code == "adapter_not_found"
    assert hidden_export.value.reason_code == "export_not_found"
    archive, _digest = service.resolve_export(exported["artifact_id"], **owner)
    assert archive.is_file()
