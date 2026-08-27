from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def test_starter_only_builds_closed_hub_request_for_needle(tmp_path) -> None:
    env = {
        **os.environ,
        "ANANTA_LOCAL_ADAPTER_OUTPUT_ROOT": str(tmp_path),
        "ANANTA_NEEDLE_BASE_MODEL_ID": "needle-base-pinned",
        "ANANTA_TRAINING_SOURCE_IDS": "SRC_approved:1",
        "ANANTA_TRAINING_RUN_IDS": "RUN_approved:1",
    }
    result = subprocess.run(
        [
            "scripts/run-local-adapter-training.sh",
            "needle2",
            "ds-0123456789abcdef0123456789abcdef",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    request_path = next(tmp_path.glob("*/request.json"))
    request = json.loads(request_path.read_text())
    assert request["backend"] == "needle"
    assert request["release_target"] == "needle2"
    assert request["dataset_id"].startswith("ds-")
    assert request["hyperparameters"]["max_sequence_length"] == 256
    assert "no training was started" in result.stdout.lower()
    assert not list(tmp_path.rglob("adapter.pkl"))


def test_starter_rejects_free_dataset_paths(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        ["scripts/run-local-adapter-training.sh", "needle2", str(dataset)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ANANTA_LOCAL_ADAPTER_OUTPUT_ROOT": str(tmp_path / "out")},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "catalog-owned dataset id" in result.stderr.lower()


def test_starter_requests_bounded_gpu_worker_for_lfm(tmp_path) -> None:
    env = {
        **os.environ,
        "ANANTA_LOCAL_ADAPTER_OUTPUT_ROOT": str(tmp_path),
        "ANANTA_LFM_SFT_BASE_MODEL_ID": "lfm-agentic-pinned",
        "ANANTA_TRAINING_SOURCE_IDS": "SRC_approved:1",
        "ANANTA_TRAINING_RUN_IDS": "RUN_approved:1",
    }
    subprocess.run(
        [
            "scripts/run-local-adapter-training.sh",
            "lfm2.5-2.6b-agentic",
            "ds-0123456789abcdef0123456789abcdef",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    request = json.loads(next(tmp_path.glob("*/request.json")).read_text())
    assert request["backend"] == "peft_trl"
    assert request["gpu_profile"] == "rtx3080-safe"
    assert request["release_target"] == "lfm2.5-2.6b-agentic"
