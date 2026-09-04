from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from ananta_contracts.research_training import canonical_json
from worker.training.research.job_runner import execute_assignment
from worker.training.tokenizers.byte_bpe import ByteBpeTrainer

from .real_helpers import assignment, dataset_manifest, persist_artifact, pipeline_spec, stage

torch = pytest.importorskip("torch")
pytest.importorskip("safetensors")


def test_two_process_cpu_ddp_and_single_process_losses_are_comparable(tmp_path: Path) -> None:
    dataset = dataset_manifest(tmp_path)
    tokenizer = ByteBpeTrainer().train(
        ["hello world hello ananta", "two plus two is four"],
        vocab_size=264,
        special_tokens=["<assistant>", "</assistant>"],
    )
    tokenizer_input = persist_artifact(tmp_path, "tokenizer", tokenizer.serialize())
    definition = stage("pretrain", "pretrain", [], "full_weight_training")

    single_spec = pipeline_spec(dataset, [definition])
    single_assignment = assignment(
        spec=single_spec,
        dataset=dataset,
        stage_definition=definition,
        inputs=[tokenizer_input],
    )
    single_assignment_path = tmp_path / "single-assignment.json"
    single_assignment_path.write_text(canonical_json(single_assignment))
    single_output = tmp_path / "single-output"
    single_output.mkdir()
    single = execute_assignment(
        assignment_path=single_assignment_path,
        input_root=tmp_path,
        output_root=single_output,
        maximum_input_bytes=20_000_000,
    )

    ddp_spec = pipeline_spec(dataset, [definition])
    ddp_spec["recipe"]["world_size"] = 2
    ddp_assignment = assignment(
        spec=ddp_spec,
        dataset=dataset,
        stage_definition=definition,
        inputs=[tokenizer_input],
    )
    ddp_assignment_path = tmp_path / "ddp-assignment.json"
    ddp_assignment_path.write_text(canonical_json(ddp_assignment))
    ddp_output = tmp_path / "ddp-output"
    ddp_output.mkdir()
    environment = {
        **os.environ,
        "ANANTA_RESEARCH_REPOSITORY_REVISION": "a" * 64,
        "ANANTA_RESEARCH_IMAGE_DIGEST": "c" * 64,
        "ANANTA_RESEARCH_HARDWARE_PROFILE_DIGEST": "d" * 64,
        "OMP_NUM_THREADS": "1",
        "PYTHONHASHSEED": "0",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "torch.distributed.run",
            "--standalone",
            "--nproc-per-node=2",
            "-m",
            "worker.training.research.job_runner",
            "--assignment",
            str(ddp_assignment_path),
            "--input-root",
            str(tmp_path),
            "--output-root",
            str(ddp_output),
            "--maximum-input-bytes",
            "20000000",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    ddp = json.loads((ddp_output / "result.json").read_text())
    single_loss = single["result"]["metrics"]["loss"]
    ddp_loss = ddp["result"]["metrics"]["loss"]
    assert ddp["result"]["metrics"]["world_size"] == 2.0
    assert abs(single_loss - ddp_loss) <= 1e-5


@pytest.mark.hardware
@pytest.mark.skipif(
    not torch.cuda.is_available() or torch.cuda.device_count() < 2,
    reason="two CUDA devices are required for the automatic GPU evidence gate",
)
def test_two_process_gpu_ddp_and_single_gpu_losses_are_comparable(tmp_path: Path) -> None:
    dataset = dataset_manifest(tmp_path)
    tokenizer = ByteBpeTrainer().train(
        ["hello world hello ananta", "two plus two is four"],
        vocab_size=264,
        special_tokens=["<assistant>", "</assistant>"],
    )
    tokenizer_input = persist_artifact(tmp_path, "tokenizer", tokenizer.serialize())
    definition = stage("pretrain", "pretrain", [], "full_weight_training")
    single_spec = pipeline_spec(dataset, [definition])
    single_payload = assignment(
        spec=single_spec,
        dataset=dataset,
        stage_definition=definition,
        inputs=[tokenizer_input],
    )
    single_payload["runtime"]["python_version"] = platform.python_version()
    single_payload["runtime"]["torch_version"] = str(torch.__version__)
    single_payload["runtime"]["cuda_version"] = str(torch.version.cuda)
    single_path = tmp_path / "gpu-single-assignment.json"
    single_path.write_text(canonical_json(single_payload))
    single_output = tmp_path / "gpu-single-output"
    single_output.mkdir()
    single = execute_assignment(
        assignment_path=single_path,
        input_root=tmp_path,
        output_root=single_output,
        maximum_input_bytes=20_000_000,
    )
    spec = pipeline_spec(dataset, [definition])
    spec["recipe"]["world_size"] = 2
    payload = assignment(
        spec=spec,
        dataset=dataset,
        stage_definition=definition,
        inputs=[tokenizer_input],
    )
    payload["runtime"]["python_version"] = platform.python_version()
    payload["runtime"]["torch_version"] = str(torch.__version__)
    payload["runtime"]["cuda_version"] = str(torch.version.cuda)
    assignment_path = tmp_path / "gpu-ddp-assignment.json"
    assignment_path.write_text(canonical_json(payload))
    output = tmp_path / "gpu-ddp-output"
    output.mkdir()
    environment = {
        **os.environ,
        "ANANTA_RESEARCH_REPOSITORY_REVISION": "a" * 64,
        "ANANTA_RESEARCH_IMAGE_DIGEST": "c" * 64,
        "ANANTA_RESEARCH_HARDWARE_PROFILE_DIGEST": "d" * 64,
        "CUDA_VISIBLE_DEVICES": "0,1",
        "PYTHONHASHSEED": "0",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "torch.distributed.run",
            "--standalone",
            "--nproc-per-node=2",
            "-m",
            "worker.training.research.job_runner",
            "--assignment",
            str(assignment_path),
            "--input-root",
            str(tmp_path),
            "--output-root",
            str(output),
            "--maximum-input-bytes",
            "20000000",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    result = json.loads((output / "result.json").read_text())
    assert result["result"]["metrics"]["world_size"] == 2.0
    assert abs(result["result"]["metrics"]["loss"] - single["result"]["metrics"]["loss"]) <= 1e-5
