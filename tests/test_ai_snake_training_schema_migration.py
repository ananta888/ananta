from __future__ import annotations

import json

from client_surfaces.operator_tui.ai_snake_training_migration import MigrationRegistry, save_with_migration


def test_migration_registry_handles_noop_and_registered_upgrade(tmp_path) -> None:
    registry = MigrationRegistry()
    registry.register(
        "ai_snake_prediction_profile.v1",
        "ai_snake_prediction_profile.v2",
        lambda payload: {**payload, "display_name": "Migrated"},
    )
    source = {"schema_version": "ai_snake_prediction_profile.v1", "profile_id": "p-default"}
    target_file = tmp_path / "profile.json"
    target_file.write_text(json.dumps(source), encoding="utf-8")

    result = save_with_migration(
        path=target_file,
        payload=source,
        target_version="ai_snake_prediction_profile.v2",
        registry=registry,
    )
    assert result.status == "ok"
    assert result.readonly is False
    payload = json.loads(target_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "ai_snake_prediction_profile.v2"
    assert payload["display_name"] == "Migrated"
    assert target_file.with_suffix(".json.bak").exists()


def test_migration_registry_unknown_future_version_is_degraded_readonly(tmp_path) -> None:
    registry = MigrationRegistry()
    payload = {"schema_version": "ai_snake_prediction_profile.v99", "profile_id": "future"}
    path = tmp_path / "future.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = save_with_migration(
        path=path,
        payload=payload,
        target_version="ai_snake_prediction_profile.v2",
        registry=registry,
    )
    assert result.status == "degraded"
    assert result.readonly is True
    assert result.reason == "unknown_future_version"
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["schema_version"] == "ai_snake_prediction_profile.v99"
