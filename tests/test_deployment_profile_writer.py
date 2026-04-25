from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.cli.deployment_profile_writer import build_deployment_profile, write_deployment_profile


def test_build_deployment_profile_marks_local_dev_non_container_default() -> None:
    payload = build_deployment_profile(
        runtime_mode="local-dev",
        runtime_profile="local-dev",
        governance_mode="safe",
        target="docker-compose",
        config_patch={"runtime_profile": "local-dev"},
    )
    assert payload["target"] == "docker-compose"
    assert payload["local_dev_default_is_non_container"] is True
    assert payload["isolation_level"] == "standard"
    assert payload["examples"]


def test_build_deployment_profile_marks_sandbox_and_strict_as_stronger_isolation() -> None:
    sandbox_payload = build_deployment_profile(
        runtime_mode="sandbox",
        runtime_profile="compose-safe",
        governance_mode="balanced",
        target="podman",
    )
    strict_payload = build_deployment_profile(
        runtime_mode="strict",
        runtime_profile="distributed-strict",
        governance_mode="strict",
        target="docker-compose",
    )
    assert sandbox_payload["isolation_level"] == "stronger"
    assert strict_payload["isolation_level"] == "stronger"


def test_write_deployment_profile_requires_confirmation_or_backup(tmp_path: Path) -> None:
    target_file = tmp_path / "deploy.json"
    target_file.write_text('{"old":true}\n', encoding="utf-8")
    payload = {"schema": "test"}
    with pytest.raises(FileExistsError):
        write_deployment_profile(
            path=target_file,
            payload=payload,
            overwrite_confirmed=False,
            backup_existing=False,
        )


def test_write_deployment_profile_creates_backup_without_force(tmp_path: Path) -> None:
    target_file = tmp_path / "deploy.json"
    target_file.write_text('{"old":true}\n', encoding="utf-8")
    payload = {"schema": "test", "version": 1}
    result = write_deployment_profile(
        path=target_file,
        payload=payload,
        overwrite_confirmed=False,
        backup_existing=True,
    )
    assert result.backup_path is not None
    backup_path = Path(result.backup_path)
    assert backup_path.exists()
    assert json.loads(target_file.read_text(encoding="utf-8"))["schema"] == "test"

