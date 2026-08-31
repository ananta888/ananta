from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.scientific_skill_runtime_control_service import (
    JsonScientificSkillRuntimeControlRepository,
    ScientificSkillRuntimeControlError,
    ScientificSkillRuntimeControlService,
)
from scripts.scientific_skill_runtime_control import main

ENTRY_ID = "skillentry_" + "a" * 64


def test_runtime_control_is_disabled_by_default_and_persists_cas_updates(tmp_path: Path) -> None:
    path = tmp_path / "runtime-control.json"
    repository = JsonScientificSkillRuntimeControlRepository(path)
    service = ScientificSkillRuntimeControlService(repository)
    assert repository.snapshot().global_enabled is False
    enabled = service.set_global(
        enabled=True,
        expected_revision=0,
        actor_id="operator-1",
        reason="start-approved-pilot",
    )
    assert enabled.revision == 1 and enabled.global_enabled is True
    disabled_entry = service.set_entry(
        entry_id=ENTRY_ID,
        enabled=False,
        expected_revision=1,
        actor_id="operator-1",
        reason="entry-under-investigation",
    )
    assert disabled_entry.revision == 2
    assert disabled_entry.entry_enabled(ENTRY_ID) is False
    assert JsonScientificSkillRuntimeControlRepository(path).snapshot() == disabled_entry


def test_stale_revision_and_tampered_state_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "runtime-control.json"
    repository = JsonScientificSkillRuntimeControlRepository(path)
    service = ScientificSkillRuntimeControlService(repository)
    service.set_global(
        enabled=True,
        expected_revision=0,
        actor_id="operator-1",
        reason="pilot",
    )
    with pytest.raises(ScientificSkillRuntimeControlError, match="revision_conflict"):
        service.set_entry(
            entry_id=ENTRY_ID,
            enabled=False,
            expected_revision=0,
            actor_id="operator-2",
            reason="stale-command",
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["global_enabled"] = False
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ScientificSkillRuntimeControlError, match="digest_invalid"):
        repository.snapshot()


def test_invalid_entry_shapes_fail_with_domain_error(tmp_path: Path) -> None:
    path = tmp_path / "runtime-control.json"
    value = {
        "schema_version": "ananta.scientific-skill-runtime-control.v1",
        "revision": 1,
        "global_enabled": True,
        "disabled_entry_ids": [123],
        "actor_id": "operator-1",
        "reason": "invalid-state",
        "state_digest": "a" * 64,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ScientificSkillRuntimeControlError, match="shape_invalid"):
        JsonScientificSkillRuntimeControlRepository(path).snapshot()


def test_cli_is_fully_automatic_and_returns_machine_readable_conflicts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "runtime-control.json"
    common = ["--state", str(path)]
    assert main([*common, "show"]) == 0
    initial = json.loads(capsys.readouterr().out)
    assert initial["state"]["revision"] == 0
    assert main(
        [
            *common,
            "global-enable",
            "--expected-revision",
            "0",
            "--actor",
            "automation",
            "--reason",
            "approved-rollout",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            *common,
            "global-disable",
            "--expected-revision",
            "0",
            "--actor",
            "automation",
            "--reason",
            "stale-emergency-command",
        ]
    ) == 2
    conflict = json.loads(capsys.readouterr().out)
    assert conflict == {
        "ok": False,
        "reason_code": "scientific_skill_runtime_control_revision_conflict",
    }
