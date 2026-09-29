from __future__ import annotations

import json
from pathlib import Path

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def test_goal_artifacts_command_requires_active_goal() -> None:
    result = execute_command(":goal artifacts", _state())
    assert result.handled is False
    assert "active goal" in result.message


def test_goal_sources_candidates_grant_revoke_detail_flow(monkeypatch, tmp_path: Path) -> None:
    from agent.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    state = execute_command(":goal use goal-cmds", _state()).state

    candidates = execute_command(":goal sources candidates", state)
    assert candidates.handled is True
    payload = json.loads(candidates.message)
    assert payload["goal_id"] == "goal-cmds"
    assert isinstance(payload["candidates"], list)

    granted = execute_command(":goal source grant sources:keycloak:snap_1 --usage use_as_context", state)
    assert granted.handled is True
    grant = json.loads(granted.message)
    grant_id = str(grant["grant_id"])
    assert grant_id.startswith("grant-")

    detail = execute_command(f":goal source detail {grant_id}", granted.state)
    detail_payload = json.loads(detail.message)
    assert detail_payload["grant_id"] == grant_id
    assert detail_payload["policy_decision_ref"]

    revoked = execute_command(f":goal source revoke {grant_id}", detail.state)
    assert revoked.handled is True
    assert "revoked" in revoked.state.status_message
