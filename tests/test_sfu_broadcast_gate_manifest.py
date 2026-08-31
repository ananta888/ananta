import json
from pathlib import Path

import pytest

from scripts.run_sfu_broadcast_gate_matrix import build_plan, validate_manifest
from scripts.sfu_broadcast_gate_common import SfuBroadcastGateError

ROOT = Path(__file__).resolve().parents[1]


def test_gate_manifest_builds_a_deterministic_task_to_artifact_plan() -> None:
    manifest = json.loads(
        (ROOT / "config/release/sfu_broadcast_gate_manifest.json").read_text(encoding="utf-8")
    )
    first = build_plan(manifest, stage="pr")
    second = build_plan(manifest, stage="pr")
    assert first == second
    assert first["gates"]
    assert all(row["task_ids"] and row["artifact"] for row in first["gates"])
    todo_gate = next(
        row for row in manifest["execution"]["gates"] if row["gate_id"] == "SFB-CI-TODO"
    )
    todo_path = ROOT / todo_gate["command"][3]
    assert todo_path == ROOT / "todos/active/todo.webrtc-sfu-broadcast-fanout.json"
    assert todo_path.is_file()


def test_gate_manifest_requires_cleanup_for_runtime_gate() -> None:
    manifest = {
        "schema": "ananta.sfu-broadcast-gate-manifest.v1",
        "manifest_version": 1,
        "execution": {
            "source_files": ["requirements.lock"],
            "stages": {"nightly": {"operator_approval_required": False}},
            "gates": [{
                "gate_id": "gate",
                "task_ids": ["task"],
                "stage": "nightly",
                "command": ["python3", "-V"],
                "artifact": "artifacts/test-gates/gate.json",
                "artifact_schema": "schema",
                "artifact_mode": "external",
                "timeout_seconds": 1,
                "cpu_seconds_max": 1,
                "memory_bytes_max": 67108864,
                "cleanup": {
                    "strategy": "owned_compose_project",
                    "commands": [],
                    "deadline_seconds": 1,
                },
                "retention_days": 1,
                "requires_real_backend": True,
                "operator_approval_required": False,
            }],
        },
    }
    with pytest.raises(SfuBroadcastGateError, match="gate_matrix_cleanup_command_missing"):
        validate_manifest(manifest)


def test_gate_manifest_never_treats_plan_as_activation() -> None:
    manifest = json.loads(
        (ROOT / "config/release/sfu_broadcast_gate_manifest.json").read_text(encoding="utf-8")
    )
    plan = build_plan(manifest, stage="container_browser")
    assert "activation_allowed" not in plan
    assert all(row["requires_real_backend"] for row in plan["gates"])
