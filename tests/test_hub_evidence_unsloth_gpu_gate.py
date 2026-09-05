import hashlib
import json
from pathlib import Path

import pytest

from scripts.lora_training_smoke_live import (
    bounded_numeric_metrics,
    load_admitted_nvidia_dataset,
    materialize_runtime_gguf,
)
from scripts.run_hub_evidence_unsloth_gpu_gate import (
    UnslothGpuGateError,
    bounded_diagnostic,
    build_container_command,
    docker_image_revision,
)
from scripts.unsloth_ollama_runtime_probe import build_ollama_container_command


def test_bounded_diagnostic_keeps_only_a_printable_tail() -> None:
    diagnostic = bounded_diagnostic("prefix\x00" + "x" * 2100 + "failure")

    assert len(diagnostic) == 2000
    assert diagnostic.endswith("failure")
    assert "\x00" not in diagnostic


def test_training_metric_projection_is_numeric_bounded_and_record_free() -> None:
    metrics = bounded_numeric_metrics(
        {
            "train": {"loss": 0.25, "steps": 1, "label": "secret"},
            "samples": ["raw-record"],
            "valid": True,
            "non_finite": float("nan"),
        }
    )

    assert metrics == {"train": {"loss": 0.25, "steps": 1}}


def test_container_command_receives_only_hub_assignment_and_immutable_inputs(tmp_path: Path) -> None:
    model = tmp_path / "tiny-causal-lm"
    model.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.write_text("", encoding="utf-8")
    devices = []
    for name in ("nvidia0", "nvidiactl", "nvidia-uvm", "nvidia-uvm-tools"):
        device = tmp_path / name
        device.write_text("", encoding="utf-8")
        devices.append(device)
    libraries = {}
    for name in ("libcuda.so.1", "libnvidia-ml.so.1", "libnvidia-ptxjitcompiler.so.1"):
        path = tmp_path / name
        path.write_text("", encoding="utf-8")
        libraries[name] = path

    command = build_container_command(
        image="worker@sha256:" + "a" * 64,
        image_id="sha256:" + "b" * 64,
        model_path=model,
        output_dir=output,
        assignment={"run_id": "RUN_bound", "source_ids": ["SRC_repo", "SRC_model"]},
        matrix_entry="matrix-entry",
        timeout_seconds=600,
        root=root,
        libraries=libraries,
        device_paths=devices,
        nvidia_smi_path=nvidia_smi,
    )

    assert "ANANTA_UNSLOTH_SRC_IDS=SRC_repo,SRC_model" in command
    assert "ANANTA_UNSLOTH_RUN_IDS=RUN_bound" in command
    assert "ANANTA_LORA_WORKER_IMAGE_SHA256=sha256:" + "b" * 64 in command
    assert "NVIDIA_VISIBLE_DEVICES=0" in command
    assert "PYTHONPATH=/app:/gate" in command
    assert "TRITON_CACHE_DIR=/tmp/triton-cache" in command
    assert "CUDA_CACHE_PATH=/tmp/cuda-cache" in command
    assert "NUMBA_CACHE_DIR=/tmp/numba-cache" in command
    assert "UNSLOTH_COMPILE_LOCATION=/tmp/unsloth-compiled-cache" in command
    assert f"{libraries['libcuda.so.1']}:/host-nvidia/libcuda.so:ro" in command
    assert "/tmp:rw,exec,nosuid,nodev,size=4294967296,uid=10005,gid=10005" in command
    assert "none" in command
    assert f"{model}:/models/tiny-causal-lm:ro" in command
    assert "--repeat" in command and command[command.index("--repeat") + 1] == "3"
    assert command[command.index("--runtime-export-dir") + 1] == "/output/runtime-exports"


def test_container_command_mounts_admitted_dataset_read_only(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    dataset_root = tmp_path / "dataset"
    recipe = dataset_root / ("a" * 64)
    recipe.mkdir(parents=True)
    result = recipe / "result.json"
    result.write_text("{}", encoding="utf-8")
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.write_text("", encoding="utf-8")

    command = build_container_command(
        image="worker:gate",
        image_id="sha256:" + "b" * 64,
        model_path=model,
        output_dir=output,
        assignment={"run_id": "RUN_bound", "source_ids": ["SRC_dataset"]},
        matrix_entry="entry",
        timeout_seconds=600,
        root=root,
        libraries={},
        device_paths=[],
        nvidia_smi_path=nvidia_smi,
        dataset_result_path=result,
    )

    assert f"{dataset_root}:/admitted-dataset:ro" in command
    assert command[command.index("--nvidia-dataset-result") + 1] == (f"/admitted-dataset/{'a' * 64}/result.json")


def test_admitted_dataset_loader_rejects_tampered_split(tmp_path: Path) -> None:
    recipe_id = "a" * 64
    recipe = tmp_path / recipe_id
    recipe.mkdir()
    train = recipe / "train.jsonl"
    validation = recipe / "validation.jsonl"
    train.write_text('{"messages":[{"role":"user","content":"a"}]}\n', encoding="utf-8")
    validation.write_text('{"messages":[{"role":"user","content":"b"}]}\n', encoding="utf-8")
    payload = {
        "schema": "ananta.unsloth-data-recipe-result.v1",
        "recipe_id": recipe_id,
        "dataset_id": "dataset",
        "dataset_hash": "b" * 64,
        "dataset_partition_sha256": "c" * 64,
        "source_id": "SRC_dataset",
        "run_id": "RUN_dataset",
        "train_ref": f"{recipe_id}/train.jsonl",
        "train_sha256": hashlib.sha256(train.read_bytes()).hexdigest(),
        "train_rows": 1,
        "validation_ref": f"{recipe_id}/validation.jsonl",
        "validation_sha256": hashlib.sha256(validation.read_bytes()).hexdigest(),
        "validation_rows": 1,
    }
    result = recipe / "result.json"
    result.write_text(json.dumps(payload), encoding="utf-8")
    train.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="nvidia_smoke_dataset_binding_invalid"):
        load_admitted_nvidia_dataset(result)


def test_admitted_dataset_loader_rejects_non_integer_row_binding(tmp_path: Path) -> None:
    recipe_id = "a" * 64
    recipe = tmp_path / recipe_id
    recipe.mkdir()
    result = recipe / "result.json"
    result.write_text(
        json.dumps(
            {
                "schema": "ananta.unsloth-data-recipe-result.v1",
                "recipe_id": recipe_id,
                "train_rows": {"untrusted": True},
                "validation_rows": 1,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="nvidia_smoke_dataset_binding_invalid"):
        load_admitted_nvidia_dataset(result)


def test_admitted_dataset_loader_rejects_result_symlink(tmp_path: Path) -> None:
    recipe_id = "a" * 64
    recipe = tmp_path / recipe_id
    recipe.mkdir()
    target = recipe / "target.json"
    target.write_text("{}", encoding="utf-8")
    result = recipe / "result.json"
    result.symlink_to(target)

    with pytest.raises(ValueError, match="nvidia_smoke_dataset_result_invalid"):
        load_admitted_nvidia_dataset(result)


def test_runtime_gguf_is_materialized_atomically_with_verified_digest(tmp_path: Path) -> None:
    source = tmp_path / "source.gguf"
    source.write_bytes(b"real-gguf-payload")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    class Runtime:
        def artifact(self, job_id, name):  # noqa: ANN001
            assert job_id == "gpu-job"
            assert name == "export-gguf-q4-k-m/model.gguf"
            return source, {"sha256": digest, "size_bytes": source.stat().st_size}

    destination = tmp_path / "provider-export"
    result = materialize_runtime_gguf(
        runtime=Runtime(),
        job_id="gpu-job",
        artifacts={
            "export-gguf-q4-k-m/model.gguf": {
                "sha256": digest,
                "size_bytes": source.stat().st_size,
            }
        },
        destination=destination,
    )

    assert result["sha256"] == digest
    assert (destination / "model.Q4_K_M.gguf").read_bytes() == source.read_bytes()
    assert not (destination / ".model.Q4_K_M.gguf.partial").exists()
    assert destination.stat().st_mode & 0o777 == 0o777


def test_ollama_command_is_gpu_bound_local_only_and_cloud_disabled(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.write_text("", encoding="utf-8")
    device = tmp_path / "nvidia0"
    device.write_text("", encoding="utf-8")
    cuda = tmp_path / "libcuda.so.1"
    cuda.write_text("", encoding="utf-8")

    command = build_ollama_container_command(
        image="ollama/ollama@sha256:" + "a" * 64,
        container_name="ananta-unsloth-ollama-0123456789abcdef",
        state_dir=state,
        libraries={"libcuda.so.1": cuda},
        device_paths=(device,),
        nvidia_smi_path=nvidia_smi,
    )

    assert "127.0.0.1::11434" in command
    assert "OLLAMA_NO_CLOUD=true" in command
    assert "NVIDIA_VISIBLE_DEVICES=0" in command
    assert "ALL" in command
    assert f"{cuda}:/host-nvidia/libcuda.so:ro" in command


def test_container_command_rejects_unreserved_assignment(tmp_path: Path) -> None:
    model = tmp_path / "tiny-causal-lm"
    model.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    nvidia_smi = tmp_path / "nvidia-smi"
    nvidia_smi.write_text("", encoding="utf-8")

    with pytest.raises(UnslothGpuGateError, match="unsloth_gate_assignment_invalid"):
        build_container_command(
            image="worker:latest",
            image_id="sha256:" + "b" * 64,
            model_path=model,
            output_dir=output,
            assignment={"run_id": "caller-run", "source_ids": []},
            matrix_entry="matrix-entry",
            timeout_seconds=600,
            root=root,
            libraries={},
            device_paths=[],
            nvidia_smi_path=nvidia_smi,
        )


def test_image_revision_requires_a_commit_bound_oci_label(monkeypatch: pytest.MonkeyPatch) -> None:
    revision = "a" * 40
    monkeypatch.setattr(
        "scripts.run_hub_evidence_unsloth_gpu_gate.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": revision})(),
    )

    assert docker_image_revision("worker:gate") == revision


def test_image_revision_rejects_an_unbound_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.run_hub_evidence_unsloth_gpu_gate.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "unbound"})(),
    )

    with pytest.raises(UnslothGpuGateError, match="unsloth_gate_worker_image_revision_invalid"):
        docker_image_revision("worker:latest")
