from __future__ import annotations

import io
import json
import multiprocessing
import time
import zipfile
from pathlib import Path

import pytest

from agent.services.ml_intern_adapter_import_service import (
    AdapterImportError,
    MlInternAdapterImportService,
)

BASE_MODEL = "local/qwen-coder-7b"


def _safetensors_bytes(value: bytes = b"\x00\x00\x00\x00") -> bytes:
    header = json.dumps(
        {"lora.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, len(value)]}},
        separators=(",", ":"),
    ).encode("utf-8")
    return len(header).to_bytes(8, "little") + header + value


def _config(base_model: str = BASE_MODEL) -> bytes:
    return json.dumps(
        {
            "base_model_name_or_path": base_model,
            "peft_type": "LORA",
            "task_type": "CAUSAL_LM",
            "r": 8,
            "lora_alpha": 16,
        }
    ).encode("utf-8")


def _archive(*, config: bytes | None = None, weights: bytes | None = None, extra=None, wrapper="") -> bytes:
    output = io.BytesIO()
    prefix = f"{wrapper.rstrip('/')}/" if wrapper else ""
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(prefix + "adapter_config.json", config if config is not None else _config())
        archive.writestr(
            prefix + "adapter_model.safetensors",
            weights if weights is not None else _safetensors_bytes(),
        )
        for name, payload in extra or []:
            archive.writestr(prefix + name, payload)
    return output.getvalue()


def _service(tmp_path: Path) -> MlInternAdapterImportService:
    return MlInternAdapterImportService(storage_root=tmp_path / "imports", clock=lambda: 1_700_000_000)


def _import(service: MlInternAdapterImportService, payload: bytes, **overrides):
    kwargs = {
        "tenant_id": "tenant",
        "principal_id": "admin",
        "stream": io.BytesIO(payload),
        "filename": "adapter.zip",
        "media_type": "application/zip",
        "adapter_id": "todo-json",
        "version": "1.0.0",
        "expected_base_model": BASE_MODEL,
        "declared_size": len(payload),
    }
    kwargs.update(overrides)
    return service.import_archive(**kwargs)


class _SlowRegistryImportService(MlInternAdapterImportService):
    def _load_registry(self, tenant_key):
        records = super()._load_registry(tenant_key)
        time.sleep(0.15)
        return records


def _import_adapter_in_process(storage_root, adapter_id, weight_byte, barrier, results):
    try:
        service = _SlowRegistryImportService(storage_root=storage_root)
        barrier.wait(timeout=10)
        imported = service.import_files(
            tenant_id="tenant",
            principal_id="admin",
            adapter_config_stream=io.BytesIO(_config()),
            adapter_weights_stream=io.BytesIO(_safetensors_bytes(bytes([weight_byte, 0, 0, 0]))),
            adapter_id=adapter_id,
            version="1.0.0",
            expected_base_model=BASE_MODEL,
        )
        results.put(imported["adapter_id"])
    except Exception as exc:  # pragma: no cover - reported in the parent process
        results.put(f"error:{exc!r}")


def test_valid_minimal_archive_and_individual_files_are_pending_evaluation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    payload = _archive(wrapper="adapter-folder")
    imported = _import(service, payload)
    assert imported["status"] == "imported_pending_evaluation"
    assert imported["base_model"] == BASE_MODEL
    assert imported["safetensors"]["tensor_count"] == 1
    assert {row["name"] for row in imported["files"]} == {
        "adapter_config.json",
        "adapter_model.safetensors",
    }
    assert "path" not in json.dumps(imported).lower()
    assert service.get_import(
        tenant_id="tenant", principal_id="admin", adapter_id="todo-json", version="1.0.0"
    ) == imported

    second = service.import_files(
        tenant_id="tenant",
        principal_id="admin",
        adapter_config_stream=io.BytesIO(_config()),
        adapter_weights_stream=io.BytesIO(_safetensors_bytes(b"\x01\x00\x00\x00")),
        adapter_id="review-style",
        version="2.0",
        expected_base_model=BASE_MODEL,
    )
    assert second["status"] == "imported_pending_evaluation"
    assert len(service.list_imports(tenant_id="tenant", principal_id="admin")) == 2


def test_import_registry_is_atomic_across_hub_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("fork")
    storage_root = tmp_path / "imports"
    barrier = context.Barrier(3)
    results = context.Queue()
    inputs = (("parallel-adapter-a", 1), ("parallel-adapter-b", 2))
    processes = [
        context.Process(
            target=_import_adapter_in_process,
            args=(storage_root, adapter_id, weight_byte, barrier, results),
        )
        for adapter_id, weight_byte in inputs
    ]
    for process in processes:
        process.start()
    barrier.wait(timeout=10)
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    imported_ids = [results.get(timeout=2) for _process in processes]
    assert not any(adapter_id.startswith("error:") for adapter_id in imported_ids)
    persisted = MlInternAdapterImportService(storage_root=storage_root).list_imports(
        tenant_id="tenant",
        principal_id="admin",
    )
    assert {row["adapter_id"] for row in persisted} == {
        "parallel-adapter-a",
        "parallel-adapter-b",
    }


def test_wrong_base_model_and_unsafe_pickle_leave_no_registry_entry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(AdapterImportError) as exc:
        _import(service, _archive(config=_config("other/model")))
    assert exc.value.reason_code == "adapter_base_model_mismatch"
    assert service.list_imports(tenant_id="tenant", principal_id="admin") == []

    with pytest.raises(AdapterImportError) as exc:
        _import(service, _archive(extra=[("pytorch_model.bin", b"pickle")]))
    assert exc.value.reason_code == "unsafe_weight_format"
    assert service.list_imports(tenant_id="tenant", principal_id="admin") == []


def test_manipulated_safetensors_and_manifest_mismatch_are_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    broken = (10_000).to_bytes(8, "little") + b"{}"
    with pytest.raises(AdapterImportError) as exc:
        _import(service, _archive(weights=broken))
    assert exc.value.reason_code == "invalid_safetensors_header"

    manifest = json.dumps(
        {
            "adapter_id": "different-id",
            "version": "1.0.0",
            "base_model": BASE_MODEL,
        }
    ).encode("utf-8")
    with pytest.raises(AdapterImportError) as exc:
        _import(service, _archive(extra=[("adapter_manifest.json", manifest)]))
    assert exc.value.reason_code == "adapter_manifest_mismatch"

    malformed_manifest = json.dumps(
        {
            "files": {
                "adapter_config.json": {
                    "sha256": "0" * 64,
                    "size_bytes": {"not": "an integer"},
                }
            }
        }
    ).encode("utf-8")
    with pytest.raises(AdapterImportError) as exc:
        _import(service, _archive(extra=[("adapter_manifest.json", malformed_manifest)]))
    assert exc.value.reason_code == "adapter_manifest_invalid"


@pytest.mark.parametrize("base_model", ["/models/qwen", "../qwen", "org/../qwen", r"C:\\models\\qwen"])
def test_client_filesystem_paths_are_not_accepted_as_base_model_ids(
    tmp_path: Path,
    base_model: str,
) -> None:
    service = _service(tmp_path)
    with pytest.raises(AdapterImportError) as exc:
        _import(
            service,
            _archive(config=_config(base_model)),
            expected_base_model=base_model,
        )
    assert exc.value.reason_code == "invalid_base_model"


def test_same_hash_is_idempotent_and_version_or_idempotency_conflicts_fail(tmp_path: Path) -> None:
    service = _service(tmp_path)
    payload = _archive()
    first = _import(service, payload, idempotency_key="import-1")
    same = _import(
        service,
        payload,
        adapter_id="different-requested-id",
        version="9.9",
        idempotency_key="another-key",
    )
    assert same["adapter_id"] == first["adapter_id"]
    assert len(service.list_imports(tenant_id="tenant", principal_id="admin")) == 1

    changed = _archive(weights=_safetensors_bytes(b"\x02\x00\x00\x00"))
    with pytest.raises(AdapterImportError) as exc:
        _import(service, changed)
    assert exc.value.reason_code == "adapter_version_conflict"

    with pytest.raises(AdapterImportError) as exc:
        _import(
            service,
            changed,
            adapter_id="other-id",
            version="2.0",
            idempotency_key="import-1",
        )
    assert exc.value.reason_code == "idempotency_conflict"


def test_archive_attack_cleans_quarantine_and_does_not_publish(tmp_path: Path) -> None:
    service = _service(tmp_path)
    attack = io.BytesIO()
    with zipfile.ZipFile(attack, "w") as archive:
        archive.writestr("../adapter_config.json", _config())
        archive.writestr("adapter_model.safetensors", _safetensors_bytes())
    with pytest.raises(AdapterImportError) as exc:
        _import(service, attack.getvalue())
    assert exc.value.reason_code == "archive_path_escape"
    assert service.list_imports(tenant_id="tenant", principal_id="admin") == []
    assert not [path for path in (tmp_path / "imports").rglob("adapter-quarantine") if any(path.iterdir())]


def test_registry_write_failure_rolls_back_promoted_files(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)

    def fail_write(_tenant_key, _records):
        raise RuntimeError("simulated registry failure")

    monkeypatch.setattr(service, "_write_registry", fail_write)
    with pytest.raises(RuntimeError, match="simulated"):
        _import(service, _archive())
    adapter_files = [path for path in (tmp_path / "imports").rglob("adapter_model.safetensors")]
    assert adapter_files == []


def test_compensation_removes_only_the_exact_request_owned_import(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first_payload = _archive()
    first = service.import_archive_with_receipt(
        tenant_id="tenant",
        principal_id="admin",
        stream=io.BytesIO(first_payload),
        filename="adapter.zip",
        media_type="application/zip",
        adapter_id="todo-json",
        version="1.0.0",
        expected_base_model=BASE_MODEL,
        declared_size=len(first_payload),
    )
    assert first.compensation_token is not None
    first_path = service.resolve_artifact_path(
        tenant_id="tenant",
        principal_id="admin",
        adapter_id="todo-json",
        version="1.0.0",
    )

    second = _import(
        service,
        _archive(weights=_safetensors_bytes(b"\x03\x00\x00\x00")),
        adapter_id="review-style",
        version="2.0",
    )
    second_path = service.resolve_artifact_path(
        tenant_id="tenant",
        principal_id="admin",
        adapter_id="review-style",
        version="2.0",
    )

    service.compensate_import(first.compensation_token)

    assert not first_path.exists()
    assert second_path.exists()
    assert service.list_imports(tenant_id="tenant", principal_id="admin") == [second]


def test_replay_revokes_creator_compensation_and_preserves_identical_artifact(tmp_path: Path) -> None:
    service = _service(tmp_path)
    payload = _archive()
    created = service.import_archive_with_receipt(
        tenant_id="tenant",
        principal_id="admin",
        stream=io.BytesIO(payload),
        filename="adapter.zip",
        media_type="application/zip",
        adapter_id="todo-json",
        version="1.0.0",
        expected_base_model=BASE_MODEL,
        declared_size=len(payload),
    )
    assert created.compensation_token is not None
    artifact_path = service.resolve_artifact_path(
        tenant_id="tenant",
        principal_id="admin",
        adapter_id="todo-json",
        version="1.0.0",
    )

    replayed = _import(service, payload, idempotency_key="replay-after-create")
    assert replayed == created.summary
    with pytest.raises(AdapterImportError) as exc:
        service.compensate_import(created.compensation_token)

    assert exc.value.reason_code == "adapter_import_compensation_stale"
    assert artifact_path.exists()
    assert service.list_imports(tenant_id="tenant", principal_id="admin") == [created.summary]


def test_compensation_refuses_to_delete_hash_changed_artifact(tmp_path: Path) -> None:
    service = _service(tmp_path)
    payload = _archive()
    created = service.import_archive_with_receipt(
        tenant_id="tenant",
        principal_id="admin",
        stream=io.BytesIO(payload),
        filename="adapter.zip",
        media_type="application/zip",
        adapter_id="todo-json",
        version="1.0.0",
        expected_base_model=BASE_MODEL,
        declared_size=len(payload),
    )
    assert created.compensation_token is not None
    artifact_path = service.resolve_artifact_path(
        tenant_id="tenant",
        principal_id="admin",
        adapter_id="todo-json",
        version="1.0.0",
    )
    (artifact_path / "adapter_model.safetensors").write_bytes(
        _safetensors_bytes(b"\x04\x00\x00\x00")
    )

    with pytest.raises(AdapterImportError) as exc:
        service.compensate_import(created.compensation_token)

    assert exc.value.reason_code == "adapter_import_compensation_hash_mismatch"
    assert artifact_path.exists()
    assert service.list_imports(tenant_id="tenant", principal_id="admin") == [created.summary]


def test_compensation_registry_failure_restores_exact_artifact_and_entry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    payload = _archive()
    created = service.import_archive_with_receipt(
        tenant_id="tenant",
        principal_id="admin",
        stream=io.BytesIO(payload),
        filename="adapter.zip",
        media_type="application/zip",
        adapter_id="todo-json",
        version="1.0.0",
        expected_base_model=BASE_MODEL,
        declared_size=len(payload),
    )
    assert created.compensation_token is not None
    artifact_path = service.resolve_artifact_path(
        tenant_id="tenant",
        principal_id="admin",
        adapter_id="todo-json",
        version="1.0.0",
    )

    def fail_write(_tenant_key, _records):
        raise OSError("simulated compensation registry failure")

    monkeypatch.setattr(service, "_write_registry", fail_write)
    with pytest.raises(AdapterImportError) as exc:
        service.compensate_import(created.compensation_token)

    assert exc.value.reason_code == "adapter_import_compensation_failed"
    assert artifact_path.exists()
    assert service.get_import(
        tenant_id="tenant",
        principal_id="admin",
        adapter_id="todo-json",
        version="1.0.0",
    ) == created.summary
    assert not list((tmp_path / "imports").rglob("adapter-compensation/*"))
