from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from agent.services.ml_intern_lora_inference_contract import (
    LoraInferenceRequest,
    MaterializedAdapter,
    approval_decision,
    build_worker_envelope,
    canonical_sha256,
)
from worker.runtime.lora_training_app import create_app
from worker.training.inference import (
    LoraInferenceRuntimeConfiguration,
    LoraInferenceWorkerError,
    LoraInferenceWorkerRuntime,
)


class _Executor:
    def __init__(self) -> None:
        self.loaded: list[tuple[Path, Path]] = []
        self.unloaded: list[Any] = []

    def availability(self):
        return True, "test runtime"

    def load(self, *, base_model_path: Path, adapter_path: Path):
        handle = (base_model_path, adapter_path)
        self.loaded.append(handle)
        return handle

    def generate(self, handle, *, prompt: str, max_new_tokens: int, temperature: float):
        return f"worker:{prompt}:{max_new_tokens}:{temperature}"

    def unload(self, handle):
        self.unloaded.append(handle)


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_sha(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(_file_sha(child).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _safetensors() -> bytes:
    data = b"adapter"
    header = json.dumps(
        {"lora.weight": {"dtype": "U8", "shape": [len(data)], "data_offsets": [0, len(data)]}},
        separators=(",", ":"),
    ).encode()
    return len(header).to_bytes(8, "little") + header + data


def _runtime_and_envelope(tmp_path: Path):
    workspace = tmp_path / "workspace"
    models = tmp_path / "models"
    adapter = workspace / "adapters" / "approved"
    model = models / "base-local"
    adapter.mkdir(parents=True)
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": "base-local", "peft_type": "LORA"}),
        encoding="utf-8",
    )
    (adapter / "adapter_model.safetensors").write_bytes(_safetensors())
    files = tuple(
        {
            "name": path.name,
            "sha256": _file_sha(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(adapter.iterdir())
    )
    materialized = MaterializedAdapter(
        adapter_id="adapter-v1",
        version="1.0",
        relative_path="adapters/approved",
        directory_sha256=canonical_sha256(list(files)),
        files=files,
    )
    request = LoraInferenceRequest(
        prompt="analyse",
        base_model="base-local",
        adapter_id="adapter-v1",
        adapter_version="1.0",
        task_kind="analysis",
        task_id="task-1",
        max_new_tokens=32,
        temperature=0.1,
    )
    approval = approval_decision(
        adapter_id="adapter-v1",
        adapter_version="1.0",
        adapter_sha256=materialized.directory_sha256,
        base_model="base-local",
        task_kind="analysis",
        approved_at="2026-07-16T00:00:00+00:00",
    )
    envelope = build_worker_envelope(
        request=request,
        adapter=materialized,
        base_model={
            "model_id": "base-local",
            "relative_path": "base-local",
            "snapshot_hash": _path_sha(model),
        },
        approval=approval,
        deadline_epoch_ms=int((time.time() + 30) * 1000),
    )
    executor = _Executor()
    runtime = LoraInferenceWorkerRuntime(
        LoraInferenceRuntimeConfiguration(
            workspace_root=workspace,
            model_root=models,
            resource_profile="cpu",
        ),
        executor=executor,
    )
    return runtime, executor, envelope


def test_worker_reverifies_binding_hashes_and_generates_then_unloads(tmp_path: Path) -> None:
    runtime, executor, envelope = _runtime_and_envelope(tmp_path)

    result = runtime.generate(envelope)
    unloaded = runtime.unload(adapter_id="adapter-v1", adapter_version="1.0")

    assert result["status"] == "succeeded"
    assert result["output"] == "worker:analyse:32:0.1"
    assert result["policy_decision"]["binding"]["status"] == "approved"
    assert len(executor.loaded) == 1
    assert unloaded["unloaded_entries"] == 1
    assert len(executor.unloaded) == 1


def test_worker_rejects_non_approved_binding_before_loading(tmp_path: Path) -> None:
    runtime, executor, envelope = _runtime_and_envelope(tmp_path)
    envelope["approval"]["binding"]["status"] = "evaluated"

    with pytest.raises(LoraInferenceWorkerError) as raised:
        runtime.generate(envelope)

    assert raised.value.reason_code == "approval_binding_mismatch"
    assert executor.loaded == []


def test_worker_rejects_adapter_hash_change_before_loading(tmp_path: Path) -> None:
    runtime, executor, envelope = _runtime_and_envelope(tmp_path)
    adapter_path = tmp_path / "workspace" / envelope["adapter"]["relative_path"] / "adapter_config.json"
    adapter_path.write_text('{"base_model_name_or_path":"tampered"}', encoding="utf-8")

    with pytest.raises(LoraInferenceWorkerError) as raised:
        runtime.generate(envelope)

    assert raised.value.reason_code == "adapter_hash_mismatch"
    assert executor.loaded == []


def test_worker_rejects_model_snapshot_symlink_before_loading(tmp_path: Path) -> None:
    runtime, executor, envelope = _runtime_and_envelope(tmp_path)
    model_path = tmp_path / "models" / envelope["base_model"]["relative_path"]
    model_path.joinpath("config-alias.json").symlink_to("config.json")

    with pytest.raises(LoraInferenceWorkerError) as raised:
        runtime.generate(envelope)

    assert raised.value.reason_code == "invalid_path"
    assert executor.loaded == []


def test_http_surface_authenticates_generation_and_confirmed_unload(tmp_path: Path) -> None:
    runtime, _executor, envelope = _runtime_and_envelope(tmp_path)
    token = "test-internal-lora-token-at-least-24-characters"
    app = create_app(
        runtime=object(),  # type: ignore[arg-type] - training routes are outside this focused test
        inference_runtime=runtime,
        auth_token=token,
    )
    client = app.test_client()
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/internal/v1/lora-training/inference/capabilities").status_code == 401
    generated = client.post(
        "/internal/v1/lora-training/inference/generate",
        json=envelope,
        headers=headers,
    )
    unloaded = client.post(
        "/internal/v1/lora-training/inference/adapters/adapter-v1/1.0/unload",
        json={"confirmed": True, "reason": "operator requested cache cleanup"},
        headers=headers,
    )

    assert generated.status_code == 200
    assert generated.get_json()["reason_code"] == "approved_adapter_inference_succeeded"
    assert unloaded.status_code == 200
    assert unloaded.get_json()["reason_code"] == "adapter_cache_unloaded"


def test_worker_inference_module_import_is_dependency_light() -> None:
    script = (
        "import sys; import worker.training.inference; "
        "forbidden={'torch','transformers','peft','trl','unsloth'}; "
        "assert not forbidden.intersection(sys.modules), forbidden.intersection(sys.modules)"
    )

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
