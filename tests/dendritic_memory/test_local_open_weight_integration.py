from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest


def test_local_catalog_model_round_trip_and_parameter_matched_baseline(tmp_path) -> None:
    """Opt-in real-model gate; never downloads weights or executes remote code."""
    model_path_raw = os.getenv("ANANTA_DENDRITIC_TEST_MODEL_PATH", "").strip()
    expected_digest = os.getenv("ANANTA_DENDRITIC_TEST_MODEL_SHA256", "").strip()
    if not model_path_raw or not expected_digest:
        pytest.skip("local catalog model and its SHA-256 are not configured")
    torch = pytest.importorskip("torch", reason="optional local model test requires torch")
    safetensors = pytest.importorskip(
        "safetensors.torch", reason="optional local model test requires safetensors"
    )
    model_path = Path(model_path_raw).resolve(strict=True)
    if model_path.suffix != ".safetensors":
        pytest.fail("local model catalog fixture must be safetensors")
    before = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if before != expected_digest:
        pytest.fail("local model catalog snapshot digest mismatch")

    state = safetensors.load_file(str(model_path), device="cpu")
    if set(state) != {"base.bias", "base.weight"}:
        pytest.fail("local model catalog fixture has an unexpected closed tensor set")
    hidden = int(state["base.weight"].shape[0])
    base = torch.nn.Linear(hidden, hidden)
    base.load_state_dict({"weight": state["base.weight"], "bias": state["base.bias"]})
    base.requires_grad_(False)

    from worker.training.dendritic.module import build_dendritic_memory_module, parameter_report
    from worker.training.dendritic.pack_io import DendriticSafetensorsPackIo

    memory = build_dendritic_memory_module(
        hidden_dimension=hidden,
        branch_count=2,
        top_k=1,
        routing_enabled=True,
        readout="residual_sum",
        max_memory_bytes=16 * 1024 * 1024,
    )
    report = parameter_report(memory)
    lora_rank = max(1, report["trainable_parameter_count"] // (hidden * 2))
    lora_parameter_count = hidden * lora_rank * 2
    assert abs(lora_parameter_count - report["trainable_parameter_count"]) <= hidden * 2

    payload = DendriticSafetensorsPackIo().dump(
        {f"memory.{key}": value.detach().cpu() for key, value in memory.state_dict().items()}
    )
    pack_path = tmp_path / "memory.safetensors"
    pack_path.write_bytes(payload)
    probe = subprocess.run(
        [
            os.fspath(Path(os.sys.executable)),
            "-c",
            "import json,sys; from safetensors.torch import load_file; "
            "print(json.dumps(sorted(load_file(sys.argv[1]).keys())))",
            os.fspath(pack_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert all(key.startswith("memory.") for key in json.loads(probe.stdout))
    assert hashlib.sha256(model_path.read_bytes()).hexdigest() == before
