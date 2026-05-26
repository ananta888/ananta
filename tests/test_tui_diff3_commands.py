from __future__ import annotations

import json

from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000", section_id="artifacts", header_logo_game={})


def test_diff3_open_and_panel_commands() -> None:
    state = _state()
    opened = execute_command(":diff3", state)
    assert opened.handled is True
    assert opened.state.section_id == "artifacts"
    payload = json.loads(opened.message)
    assert payload["diff3_mode"] is True

    panel_a = execute_command(":diff3 panel A current", opened.state)
    panel_b = execute_command(":diff3 panel B current --mode summary", panel_a.state)
    panel_c = execute_command(":diff3 panel C ai review", panel_b.state)
    raw_state = panel_c.state.header_logo_game["diff3_state"]
    rows = {item["panel_id"]: item for item in raw_state["panels"]}
    assert rows["A"]["render_mode"] == "unified"
    assert rows["B"]["render_mode"] == "summary"
    assert rows["C"]["panel_type"] == "ai_review"


def test_diff3_focus_scroll_and_sync() -> None:
    state = execute_command(":diff3", _state()).state
    focused = execute_command(":diff3 focus B", state)
    synced = execute_command(":diff3 sync on", focused.state)
    scrolled = execute_command(":diff3 scroll pagedown", synced.state)
    raw_state = scrolled.state.header_logo_game["diff3_state"]
    assert raw_state["active_panel"] == "B"
    assert raw_state["extensions"]["sync_scroll"] is True
    panel_b = next(item for item in raw_state["panels"] if item["panel_id"] == "B")
    assert panel_b["scroll_state"]["line"] >= 20


def test_diff3_panel_filter_and_mode_switch() -> None:
    state = execute_command(":diff3", _state()).state
    updated = execute_command(":diff3 panel A filter path_filter=src/ status_filter=modified", state)
    switched = execute_command(":diff3 panel A mode files_only", updated.state)
    raw_state = switched.state.header_logo_game["diff3_state"]
    panel_a = next(item for item in raw_state["panels"] if item["panel_id"] == "A")
    assert panel_a["render_mode"] == "files_only"
    assert panel_a["filters"]["path_filter"] == "src/"
    assert panel_a["filters"]["status_filter"] == "modified"


def test_diff3_rejects_invalid_panel_id() -> None:
    response = execute_command(":diff3 panel Z current", _state())
    assert response.handled is False
    assert "invalid panel id" in response.message


def test_diff3_ai_mode_state_switch() -> None:
    state = execute_command(":diff3", _state()).state
    with_ai = execute_command(":diff3 panel C ai explain", state)
    switched = execute_command(":diff3 ai patch", with_ai.state)
    raw_state = switched.state.header_logo_game["diff3_state"]
    ai_state = raw_state["extensions"]["ai_panel_state"]
    assert ai_state["mode"] == "patch"
    assert ai_state["prompt_template_ref"] == "prompt:diff3/patch"


def test_diff3_panel_output_source_and_ai_run(monkeypatch) -> None:
    def _ok(*, goal_id: str | None, diff3_state: dict, mode: str) -> dict:
        return {
            "status": "success",
            "reason_code": "",
            "response": {
                "schema": "ai_diff_response.v1",
                "status": "success",
                "artifact_type": mode,
                "summary": "ok",
                "findings": [],
                "risks": [],
                "suggested_tests": [],
                "patch_suggestions": [],
                "source_refs": [],
            },
            "context_envelope": {"selected_hunk_refs": []},
            "provenance_id": "prov-1",
            "output_artifact_id": "out-1",
        }

    monkeypatch.setattr("client_surfaces.operator_tui.commands.dispatch_ai_diff_request", _ok)
    state = execute_command(":goal use goal-1", _state()).state
    output_set = execute_command(":diff3 panel A output out-1", state)
    assert output_set.handled is True
    run = execute_command(":diff3 ai run review", output_set.state)
    assert run.handled is True
    raw_state = run.state.header_logo_game["diff3_state"]
    panel_a = next(item for item in raw_state["panels"] if item["panel_id"] == "A")
    assert panel_a["source_left"]["source_kind"] == "goal_output_artifact"
    assert panel_a["source_left"]["locator"]["output_artifact_id"] == "out-1"
