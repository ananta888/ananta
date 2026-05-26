from __future__ import annotations

import json

from client_surfaces.operator_tui.ai_snake_training_store import (
    append_behavior_event,
    build_training_bundle,
    compact_training_data,
    data_path_status,
    data_show_status,
    delete_events,
    delete_patterns,
    ensure_training_layout,
    pattern_detail,
    patterns_status_lines,
    read_patterns,
    reset_training_data,
    save_patterns,
    training_paths,
)
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorState


def test_training_layout_uses_config_ai_snake_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = ensure_training_layout()
    assert str(paths["base_dir"]).endswith("/.config/ananta/ai_snake")
    assert paths["active_profile"].exists()
    assert paths["learned_patterns"].exists()
    assert paths["exports_dir"].exists()


def test_ai_data_path_command_shows_all_expected_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})
    result = execute_command(":ai data path", state)
    assert result.handled is True
    msg = result.state.status_message
    assert "ai-data base=" in msg
    assert "prediction_profile.active.json" in msg
    assert "prediction_events.jsonl" in msg
    assert "learned_patterns.json" in msg
    assert "exports=" in msg
    # also ensure path helper is stable and non-empty
    assert data_path_status()
    assert training_paths()["base_dir"].name == "ai_snake"


def test_ai_data_show_and_patterns_commands(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns(
        [
            {
                "schema_version": "ai_snake_learned_pattern.v1",
                "pattern_id": "pat-open-artifacts",
                "status": "active",
                "confidence": 0.72,
                "human_explanation": "open artifacts after note",
                "ai_hint": "suggest artifacts",
                "condition": {"all": [{"field": "last_section", "op": "eq", "value": "notes"}]},
                "outcome": {"intent_kind": "open_artifact", "target_ref": "section:artifacts"},
                "evidence": {"sample_size": 3, "event_refs": ["evt-1"], "privacy_class": "workspace"},
                "counters": {"hits": 3, "successes": 2, "false_positives": 0},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
    )
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})
    show = execute_command(":ai data show", state)
    assert show.handled is True
    assert "ai-data profile=" in str(show.state.status_message or "")
    patterns = execute_command(":ai patterns", show.state)
    assert patterns.handled is True
    assert "pat-open-artifacts" in patterns.message
    detail = execute_command(":ai pattern pat-open-artifacts", patterns.state)
    assert detail.handled is True
    assert "pattern=pat-open-artifacts" in detail.message
    assert patterns_status_lines()
    assert "pattern=pat-open-artifacts" in pattern_detail("pat-open-artifacts")


def test_ai_data_export_stdout_json(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})
    result = execute_command(":ai data export --stdout --format json", state)
    assert result.handled is True
    payload = json.loads(result.message)
    assert payload["schema_version"] == "ai_snake_training_bundle.v1"
    assert "profile" in payload
    assert "patterns" in payload
    assert payload["checksums"]["profile_sha256"]
    direct = build_training_bundle(include_events=False)
    assert direct["schema_version"] == "ai_snake_training_bundle.v1"


def test_ai_learning_command_updates_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})
    on = execute_command(":ai learning on", state)
    assert on.handled is True
    assert "learning on" in on.message
    off = execute_command(":ai learning off", on.state)
    assert off.handled is True
    assert "learning off" in off.message
    assert "enabled=False" in execute_command(":ai learning status", off.state).message


def test_training_layout_creates_readme_without_overwrite(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = ensure_training_layout()
    readme = paths["readme"]
    assert readme.exists()
    readme.write_text("custom", encoding="utf-8")
    ensure_training_layout()
    assert readme.read_text(encoding="utf-8") == "custom"


def test_ai_data_export_to_file_and_compact(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})
    out = tmp_path / "bundle.json"
    export = execute_command(f":ai data export {out} --format json", state)
    assert export.handled is True
    assert out.exists()
    compact = execute_command(":ai data compact", export.state)
    assert compact.handled is True
    assert "compact" in (compact.state.status_message or "")
    direct = compact_training_data()
    assert "patterns_total" in direct


def test_ai_data_delete_and_reset_commands(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = ensure_training_layout()
    append_behavior_event(event_type="section_visit", value_norm="dashboard", refs=["section:dashboard"], privacy_class="public_ui")
    save_patterns(
        [
            {
                "pattern_id": "pat-x",
                "pattern_type": "heuristic",
                "conditions": {"field": "section_is", "op": "eq", "value": "dashboard"},
                "predicted_intent": "navigate",
                "confidence": 0.2,
                "evidence": {"source_event_ids": [], "sample_size": 0, "counter_refs": []},
                "counters": {"hits": 0, "misses": 0, "positives": 0, "negatives": 0},
                "last_seen_at": "2026-01-01T00:00:00Z",
                "expires_at": "2026-01-02T00:00:00Z",
                "status": "draft",
                "human_explanation": "x",
                "ai_hint": "y",
            }
        ]
    )
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})
    delete_ev = execute_command(":ai data delete events", state)
    assert delete_ev.handled is True
    assert paths["events_log"].read_text(encoding="utf-8") == ""
    delete_pat = execute_command(":ai data delete patterns", delete_ev.state)
    assert delete_pat.handled is True
    assert read_patterns() == []
    reset = execute_command(":ai data reset", delete_pat.state)
    assert reset.handled is True
    assert "reset" in reset.message
    assert reset_training_data()
    assert delete_events()
    assert delete_patterns()


def test_ai_prediction_feedback_command_records_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns(
        [
            {
                "pattern_id": "pat-feedback",
                "pattern_type": "sequence_rule",
                "conditions": {"all": [{"field": "recent_event", "op": "contains", "value": "artifact:README.md"}]},
                "predicted_intent": "artifact_explain",
                "confidence": 0.5,
                "evidence": {"source_event_ids": ["evt-1"], "sample_size": 1, "counter_refs": ["counter:pat-feedback"]},
                "counters": {"hits": 1, "misses": 0, "positives": 1, "negatives": 0},
                "last_seen_at": "2026-01-01T00:00:00Z",
                "expires_at": "2026-02-01T00:00:00Z",
                "status": "draft",
                "human_explanation": "x",
                "ai_hint": "y",
            }
        ]
    )
    state = OperatorState(
        endpoint="http://localhost:5000",
        header_logo_game={"ai_snake_prediction": {"target_ref": "README.md"}},
    )
    good = execute_command(":ai prediction good", state)
    bad = execute_command(":ai prediction bad wrong-target", good.state)
    assert good.handled is True
    assert bad.handled is True
    events_content = ensure_training_layout()["events_log"].read_text(encoding="utf-8")
    assert "prediction_feedback" in events_content


def test_ai_data_export_md_and_import_preview_commands(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    state = OperatorState(endpoint="http://localhost:5000", header_logo_game={})
    md = tmp_path / "report.md"
    export_md = execute_command(f":ai data export-md {md}", state)
    assert export_md.handled is True
    assert md.exists()
    bundle = tmp_path / "bundle.json"
    export_json = execute_command(f":ai data export {bundle} --format json", export_md.state)
    assert export_json.handled is True
    preview = execute_command(f":ai data import {bundle} --preview", export_json.state)
    assert preview.handled is True
    payload = json.loads(preview.message)
    assert payload["status"] == "preview"
