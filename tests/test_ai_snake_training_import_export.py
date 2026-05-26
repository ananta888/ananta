from __future__ import annotations

import json
from pathlib import Path

from client_surfaces.operator_tui.ai_snake_training_import_export import (
    export_training_bundle_to_path,
    export_training_markdown,
    import_training_bundle,
    preview_training_bundle,
)
from client_surfaces.operator_tui.ai_snake_training_store import (
    ensure_training_layout,
    read_active_profile,
    read_patterns,
    save_patterns,
)


def _pattern(pattern_id: str, confidence: float, *, explanation: str = "pattern") -> dict:
    return {
        "pattern_id": pattern_id,
        "pattern_type": "sequence_rule",
        "conditions": {"all": [{"field": "recent_event", "op": "contains", "value": "artifact:README.md"}]},
        "predicted_intent": "artifact_explain",
        "confidence": confidence,
        "evidence": {"source_event_ids": [f"evt-{pattern_id}"], "sample_size": 1, "counter_refs": [f"counter:{pattern_id}"]},
        "counters": {"hits": 1, "misses": 0, "positives": 1, "negatives": 0},
        "last_seen_at": "2026-01-01T00:00:00Z",
        "expires_at": "2026-02-01T00:00:00Z",
        "status": "active",
        "human_explanation": explanation,
        "ai_hint": "",
    }


def test_export_markdown_report_contains_sections(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns([_pattern("pat-a", 0.8)])
    md = export_training_markdown(output_path=str(tmp_path / "report.md"), json_ref="bundle.json")
    text = md.read_text(encoding="utf-8")
    assert "AI-Snake Training Report" in text
    assert "Datenschutz" in text
    assert "JSON-Export: `bundle.json`" in text
    assert "pat-a" in text


def test_import_preview_does_not_mutate_local_state(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns([_pattern("pat-local", 0.2, explanation="local")])
    bundle_path = export_training_bundle_to_path(output_path=str(tmp_path / "bundle.json"))
    before = list(read_patterns())
    preview = import_training_bundle(input_path=str(bundle_path), preview=True)
    after = list(read_patterns())
    assert preview["status"] == "preview"
    assert before == after


def test_import_conflict_strategies(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns([_pattern("pat-shared", 0.2, explanation="local")])
    incoming = {
        "bundle_id": "b1",
        "schema_version": "ai_snake_training_bundle.v1",
        "exported_at": "2026-01-01T00:00:00Z",
        "source": {"app": "ananta-tui", "version": "1.0"},
        "profile": read_active_profile(),
        "patterns": [_pattern("pat-shared", 0.9, explanation="remote")],
        "checksums": {"profile_sha256": "0" * 64, "patterns_sha256": "1" * 64},
        "human_readme": "h",
        "ai_readme": "a",
        "privacy_manifest": {"public_ui": 0, "workspace": 1, "private_local": 0, "sensitive_blocked": 0},
    }
    src = tmp_path / "incoming.json"
    src.write_text(json.dumps(incoming, ensure_ascii=False), encoding="utf-8")

    keep_best = import_training_bundle(input_path=str(src), preview=False)
    assert keep_best["conflicts"] >= 1
    assert "keep_higher_confidence" in keep_best["conflict_resolution"]
    assert abs(float(read_patterns()[0]["confidence"]) - 0.9) < 1e-9

    save_patterns([_pattern("pat-shared", 0.2, explanation="local")])
    keep_local = import_training_bundle(input_path=str(src), preview=False, conflict_strategy="keep_local")
    assert "keep_local" in keep_local["conflict_resolution"]
    assert abs(float(read_patterns()[0]["confidence"]) - 0.2) < 1e-9

    save_patterns([_pattern("pat-shared", 0.2, explanation="local")])
    merged = import_training_bundle(input_path=str(src), preview=False, conflict_strategy="merge_counters")
    assert "merge_counters" in merged["conflict_resolution"]
    assert int(read_patterns()[0]["counters"]["hits"]) >= 2


def test_import_handles_future_schema_as_degraded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    ensure_training_layout()
    payload = {
        "bundle_id": "future",
        "schema_version": "ai_snake_training_bundle.v9",
        "exported_at": "2026-01-01T00:00:00Z",
        "source": {"app": "ananta-tui", "version": "1.0"},
        "profile": read_active_profile(),
        "patterns": [],
        "checksums": {"profile_sha256": "0" * 64, "patterns_sha256": "1" * 64},
        "human_readme": "h",
        "ai_readme": "a",
        "privacy_manifest": {"public_ui": 0, "workspace": 0, "private_local": 0, "sensitive_blocked": 0},
    }
    target = tmp_path / "future.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = import_training_bundle(input_path=str(target), preview=False)
    assert result["status"] == "degraded"
    assert result["readonly"] is True


def test_roundtrip_export_import_restores_patterns(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns([_pattern("pat-rt", 0.77)])
    exported = export_training_bundle_to_path(output_path=str(tmp_path / "roundtrip.json"))
    save_patterns([])
    imported = import_training_bundle(input_path=str(exported), preview=False, conflict_strategy="overwrite")
    assert imported["status"] == "imported"
    assert any(str(item.get("pattern_id") or "") == "pat-rt" for item in read_patterns())
    preview = preview_training_bundle(str(exported))
    assert preview["patterns"] >= 1


def test_save_patterns_updates_profile_ai_summary_and_hint_standard(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    save_patterns([_pattern("pat-summary", 0.65)])
    profile = read_active_profile()
    assert "learning=" in str(profile.get("ai_summary") or "")
    assert "patterns=1" in str(profile.get("ai_summary") or "")
    hint = str(read_patterns()[0].get("ai_hint") or "")
    assert hint
