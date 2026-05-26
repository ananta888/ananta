from __future__ import annotations

from agent.services.config_snapshot_service import ConfigSnapshotService


def test_config_snapshot_hash_stable_for_same_payload() -> None:
    service = ConfigSnapshotService()
    payload = {"worker": "default", "secret": "do-not-store", "nested": {"token": "abc"}}
    first = service.build_snapshot(
        config_kind="worker_config",
        source_path_or_ref="config/worker.yaml",
        scope="goal:goal-1",
        config_payload=payload,
    )
    second = service.build_snapshot(
        config_kind="worker_config",
        source_path_or_ref="config/worker.yaml",
        scope="goal:goal-1",
        config_payload=payload,
    )
    assert first["config_hash"] == second["config_hash"]
    assert first["redacted_config_hash"] == second["redacted_config_hash"]


def test_config_snapshot_hash_changes_when_payload_changes() -> None:
    service = ConfigSnapshotService()
    baseline = service.build_snapshot(
        config_kind="runtime_config",
        source_path_or_ref="runtime/default.json",
        scope="goal:goal-1",
        config_payload={"runtime": "python", "threads": 2},
    )
    changed = service.build_snapshot(
        config_kind="runtime_config",
        source_path_or_ref="runtime/default.json",
        scope="goal:goal-1",
        config_payload={"runtime": "python", "threads": 4},
    )
    assert baseline["config_hash"] != changed["config_hash"]


def test_config_snapshot_redaction_uses_distinct_hash() -> None:
    service = ConfigSnapshotService()
    snapshot = service.build_snapshot(
        config_kind="model_config",
        source_path_or_ref="models/local.yaml",
        scope="goal:goal-1",
        config_payload={"model": "gpt", "password": "secret"},
    )
    assert snapshot["config_hash"] != snapshot["redacted_config_hash"]
