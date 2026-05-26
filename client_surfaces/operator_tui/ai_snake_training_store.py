from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_learning import compact_event_log, merge_patterns, mine_patterns_from_events
from client_surfaces.operator_tui.ai_snake_training_data import (
    BEHAVIOR_EVENT_SCHEMA_FILE,
    TRAINING_BUNDLE_SCHEMA_FILE,
    default_profile,
    validate_payload,
    validate_prediction_profile,
)


def training_base_dir() -> Path:
    return Path.home() / ".config" / "ananta" / "ai_snake"


def training_paths() -> dict[str, Path]:
    base = training_base_dir()
    return {
        "base_dir": base,
        "active_profile": base / "prediction_profile.active.json",
        "events_log": base / "prediction_events.jsonl",
        "learned_patterns": base / "learned_patterns.json",
        "exports_dir": base / "exports",
        "readme": base / "README.md",
        "audit_log": base / "training_import_audit.log",
    }


def ensure_training_layout() -> dict[str, Path]:
    paths = training_paths()
    paths["base_dir"].mkdir(parents=True, exist_ok=True)
    paths["exports_dir"].mkdir(parents=True, exist_ok=True)
    if not paths["active_profile"].exists():
        profile = default_profile()
        errors = validate_prediction_profile(profile)
        if errors:
            raise ValueError(f"default training profile invalid: {errors[0]}")
        paths["active_profile"].write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not paths["learned_patterns"].exists():
        payload = {"schema_version": "ai_snake_learned_pattern.v1-list", "patterns": [], "updated_at": _now_iso()}
        paths["learned_patterns"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ensure_training_readme(force=False)
    return paths


def read_active_profile() -> dict[str, Any]:
    paths = ensure_training_layout()
    try:
        payload = json.loads(paths["active_profile"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = default_profile()
    return payload if isinstance(payload, dict) else default_profile()


def data_path_status() -> str:
    paths = ensure_training_layout()
    return (
        f"ai-data base={paths['base_dir']} "
        f"profile={paths['active_profile']} "
        f"events={paths['events_log']} "
        f"patterns={paths['learned_patterns']} "
        f"exports={paths['exports_dir']}"
    )


def ensure_training_readme(*, force: bool = False) -> Path:
    paths = training_paths()
    target = paths["readme"]
    if target.exists() and not force:
        return target
    target.write_text(
        (
            "# AI-Snake Training Data\n\n"
            "- `prediction_profile.active.json`: aktives Prediction-Profil\n"
            "- `prediction_events.jsonl`: normalisierte Behavior-Events (optional)\n"
            "- `learned_patterns.json`: gelernte Muster\n"
            "- `exports/`: exportierte Bundles/Reports\n\n"
            "TUI-Kommandos:\n"
            "- `:ai data path`\n"
            "- `:ai data show`\n"
            "- `:ai patterns`\n"
            "- `:ai pattern <id>`\n"
            "- `:ai data export --stdout --format json [--include-events]`\n\n"
            "Hinweis: Exportdateien können Nutzungsverhalten enthalten.\n"
        ),
        encoding="utf-8",
    )
    return target


def save_active_profile(profile: dict[str, Any], *, backup: bool = False) -> None:
    paths = ensure_training_layout()
    profile_out = dict(profile)
    profile_out["updated_at"] = _now_iso()
    errors = validate_prediction_profile(profile_out)
    if errors:
        raise ValueError(f"invalid profile: {errors[0]}")
    if backup and paths["active_profile"].exists():
        shutil.copy2(paths["active_profile"], paths["active_profile"].with_suffix(".json.bak"))
    paths["active_profile"].write_text(json.dumps(profile_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_patterns() -> list[dict[str, Any]]:
    paths = ensure_training_layout()
    try:
        payload = json.loads(paths["learned_patterns"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    patterns = payload.get("patterns")
    if not isinstance(patterns, list):
        return []
    return [item for item in patterns if isinstance(item, dict)]


def save_patterns(patterns: list[dict[str, Any]], *, backup: bool = False) -> None:
    paths = ensure_training_layout()
    normalized = [_normalize_pattern(item) for item in patterns if isinstance(item, dict)]
    payload = {"schema_version": "ai_snake_learned_pattern.v1-list", "updated_at": _now_iso(), "patterns": normalized}
    if backup and paths["learned_patterns"].exists():
        shutil.copy2(paths["learned_patterns"], paths["learned_patterns"].with_suffix(".json.bak"))
    paths["learned_patterns"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _sync_profile_summaries(patterns=normalized)


def read_events(*, max_items: int = 2000) -> list[dict[str, Any]]:
    paths = ensure_training_layout()
    if not paths["events_log"].exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = paths["events_log"].read_text(encoding="utf-8").splitlines()
    for raw in lines[-max(1, int(max_items)) :]:
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def append_behavior_event(
    *,
    event_type: str,
    value_norm: str,
    refs: list[str] | None = None,
    privacy_class: str = "workspace",
    retention_hint: str = "rolling_30d",
    reason: str = "",
) -> bool:
    paths = ensure_training_layout()
    refs_list = [str(item).strip() for item in (refs or []) if str(item).strip()][:8]
    mapped_type = {
        "section_visit": "section_change",
        "movement_vector": "movement",
        "artifact_focus": "artifact_selected",
        "notes_usage": "notes_state",
    }.get(str(event_type or "").strip(), str(event_type or "").strip())
    mapped_retention = {
        "ephemeral": "ephemeral",
        "rolling_7d": "standard",
        "rolling_30d": "long",
        "short": "short",
        "standard": "standard",
        "long": "long",
    }.get(str(retention_hint or "").strip(), "standard")
    refs_obj: dict[str, str] = {}
    for value in refs_list:
        if value.startswith("section:"):
            refs_obj["section_ref"] = value
        elif "/" in value or "." in value:
            refs_obj["artifact_ref"] = value
        elif value.startswith("cmd:"):
            refs_obj["command_ref"] = value
    normalized_value = str(value_norm or "").strip()[:200]
    if str(privacy_class or "") == "private_local":
        normalized_value = "notes_active" if normalized_value else "private_event"
    payload = {
        "event_id": f"evt_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
        "created_at": _now_iso(),
        "event_type": mapped_type,
        "source": "feedback" if mapped_type == "prediction_feedback" else "tui",
        "normalized_value": normalized_value or "-",
        "refs": refs_obj,
        "privacy_class": str(privacy_class or "workspace"),
        "retention_hint": mapped_retention,
        "human_label": str(reason or "")[:160] if reason else None,
    }
    if payload["human_label"] is None:
        payload.pop("human_label")
    errors = validate_payload(payload, schema_filename=BEHAVIOR_EVENT_SCHEMA_FILE)
    if errors:
        return False
    with paths["events_log"].open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return True


def count_event_lines() -> int:
    paths = ensure_training_layout()
    if not paths["events_log"].exists():
        return 0
    count = 0
    for line in paths["events_log"].read_text(encoding="utf-8").splitlines():
        if line.strip():
            count += 1
    return count


def build_training_bundle(*, include_events: bool = False) -> dict[str, Any]:
    paths = ensure_training_layout()
    profile = read_active_profile()
    patterns = _bundle_safe_patterns(read_patterns())
    events_sample: list[dict[str, Any]] = []
    privacy_manifest = {"public_ui": 0, "workspace": 0, "private_local": 0, "sensitive_blocked": 0}
    if include_events and paths["events_log"].exists():
        for raw in paths["events_log"].read_text(encoding="utf-8").splitlines()[-200:]:
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            events_sample.append(item)
            pclass = str(item.get("privacy_class") or "")
            if pclass in privacy_manifest:
                privacy_manifest[pclass] += 1
    for pattern in patterns:
        pclass = str(((pattern.get("evidence") or {}).get("privacy_class")) or "workspace")
        if pclass in privacy_manifest:
            privacy_manifest[pclass] += 1
    profile_sha = _sha256_json(profile)
    patterns_sha = _sha256_json(patterns)
    bundle = {
        "bundle_id": f"ai-snake-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        "schema_version": "ai_snake_training_bundle.v1",
        "exported_at": _now_iso(),
        "source": {"app": "ananta-tui", "version": "1.0"},
        "profile": profile,
        "patterns": patterns,
        "checksums": {"profile_sha256": profile_sha, "patterns_sha256": patterns_sha},
        "human_readme": "AI-Snake Trainingsbundle mit Profil und Patterns.",
        "ai_readme": "Use profile + patterns as compact behavior-training context.",
        "privacy_manifest": privacy_manifest,
        "extensions": {},
    }
    if privacy_manifest.get("private_local", 0) > 0:
        bundle["extensions"]["export_warning"] = "private_local data included by explicit configuration"
    if include_events:
        bundle["events_sample"] = events_sample
        bundle["checksums"]["events_sha256"] = _sha256_json(events_sample)
    errors = validate_payload(bundle, schema_filename=TRAINING_BUNDLE_SCHEMA_FILE)
    if errors:
        raise ValueError(f"invalid bundle: {errors[0]}")
    return bundle


def compact_training_data(
    *,
    max_event_bytes: int = 5 * 1024 * 1024,
    min_cases: int = 3,
    backup: bool = True,
) -> dict[str, Any]:
    paths = ensure_training_layout()
    events = read_events(max_items=5000)
    mined = mine_patterns_from_events(events=events, min_cases=min_cases)
    merged = merge_patterns(existing=read_patterns(), mined=mined)
    save_patterns(merged, backup=backup)
    compaction = compact_event_log(
        events_path=paths["events_log"],
        max_bytes=max_event_bytes,
        keep_last_lines=500,
        backup=backup,
    )
    return {
        "patterns_total": len(merged),
        "patterns_mined": len(mined),
        "event_before_bytes": int(compaction.get("before_bytes") or 0),
        "event_after_bytes": int(compaction.get("after_bytes") or 0),
        "event_kept_lines": int(compaction.get("kept_lines") or 0),
        "backup": bool(backup),
    }


def delete_events(*, backup: bool = True) -> bool:
    paths = ensure_training_layout()
    if not paths["events_log"].exists():
        return True
    if backup:
        shutil.copy2(paths["events_log"], paths["events_log"].with_suffix(".jsonl.bak"))
    paths["events_log"].write_text("", encoding="utf-8")
    return True


def delete_patterns(*, backup: bool = True) -> bool:
    if backup:
        save_patterns(read_patterns(), backup=True)
    save_patterns([], backup=False)
    return True


def reset_training_data(*, backup: bool = True) -> dict[str, Any]:
    profile = default_profile()
    save_active_profile(profile, backup=backup)
    delete_patterns(backup=backup)
    delete_events(backup=backup)
    return {"profile_id": str(profile.get("profile_id") or "default"), "backup": bool(backup)}


def data_show_status() -> str:
    paths = ensure_training_layout()
    profile = read_active_profile()
    patterns = read_patterns()
    events_count = count_event_lines()
    learning = dict(profile.get("learning_settings") or {})
    enabled = bool(learning.get("enabled"))
    paused = bool(learning.get("paused"))
    mode = "paused" if paused else ("on" if enabled else "off")
    return (
        f"ai-data profile={profile.get('display_name') or 'unknown'} "
        f"patterns={len(patterns)} events={events_count} "
        f"learning={mode} updated={profile.get('updated_at') or '-'} path={paths['active_profile']}"
    )


def patterns_status_lines(*, max_items: int = 10) -> list[str]:
    rows: list[str] = []
    patterns = read_patterns()
    if not patterns:
        return ["patterns: none"]
    for item in patterns[: max(1, int(max_items))]:
        rows.append(
            f"{item.get('pattern_id')}: [{item.get('status')}] conf={float(item.get('confidence') or 0.0):.2f} "
            f"{str(item.get('human_explanation') or '')[:120]}"
        )
    return rows


def pattern_detail(pattern_id: str) -> str:
    key = str(pattern_id).strip()
    if not key:
        return "pattern id required"
    for item in read_patterns():
        if str(item.get("pattern_id") or "") == key:
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            return (
                f"pattern={key} status={item.get('status')} conf={float(item.get('confidence') or 0.0):.2f} "
                f"hint={str(item.get('ai_hint') or '')[:200]} "
                f"evidence_sample={int(evidence.get('sample_size') or 0)}"
            )
    return f"pattern not found: {key}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def payload_sha256(payload: Any) -> str:
    return _sha256_json(payload)


def append_training_audit_log(entry: dict[str, Any]) -> None:
    paths = ensure_training_layout()
    line = json.dumps(entry, ensure_ascii=False)
    with paths["audit_log"].open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _sync_profile_summaries(*, patterns: list[dict[str, Any]]) -> None:
    profile = read_active_profile()
    learning = dict(profile.get("learning_settings") or {})
    enabled = bool(learning.get("enabled", True))
    paused = bool(learning.get("paused", False))
    active = [item for item in patterns if str(item.get("status") or "") == "active"]
    intent_counts: dict[str, int] = {}
    for item in patterns:
        intent = str(item.get("predicted_intent") or "unknown")
        intent_counts[intent] = intent_counts.get(intent, 0) + 1
    top_intent = "none"
    if intent_counts:
        top_intent = sorted(intent_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    mode = "paused" if paused else ("on" if enabled else "off")
    profile["pattern_refs"] = [str(item.get("pattern_id") or "") for item in patterns if str(item.get("pattern_id") or "")]
    profile["human_summary"] = (
        f"{len(patterns)} Pattern(s), {len(active)} aktiv, Top-Intent: {top_intent}."
    )
    profile["ai_summary"] = (
        f"learning={mode}; patterns={len(patterns)}; active={len(active)}; top={top_intent}"
    )
    save_active_profile(profile, backup=False)


def _normalize_pattern(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    intent = str(out.get("predicted_intent") or "unknown")
    if not str(out.get("ai_hint") or "").strip():
        out["ai_hint"] = _standard_ai_hint(predicted_intent=intent, target_ref=str(out.get("target_ref") or ""))
    return out


def _bundle_safe_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for item in patterns:
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        if str(evidence.get("privacy_class") or "") == "sensitive_blocked":
            continue
        safe.append(item)
    return safe


def _standard_ai_hint(*, predicted_intent: str, target_ref: str) -> str:
    key = str(predicted_intent or "unknown")
    if key == "artifact_explain":
        return f"Explain selected artifact context first (target={target_ref or 'artifact'})."
    if key == "chat_help":
        return "Provide concise help in chat before broad navigation."
    if key == "notes_resume":
        return "Resume notes workflow without exposing private note contents."
    if key == "config_edit":
        return "Suggest the safest configuration edit path first."
    if key == "navigate":
        return "Prefer clear navigation hints to the likely next section."
    return "Use deterministic local guidance and ask for confirmation when unclear."
