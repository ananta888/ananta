from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_training_data import (
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
    payload = json.loads(paths["active_profile"].read_text(encoding="utf-8"))
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
    payload = json.loads(paths["learned_patterns"].read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    patterns = payload.get("patterns")
    if not isinstance(patterns, list):
        return []
    return [item for item in patterns if isinstance(item, dict)]


def save_patterns(patterns: list[dict[str, Any]], *, backup: bool = False) -> None:
    paths = ensure_training_layout()
    payload = {"schema_version": "ai_snake_learned_pattern.v1-list", "updated_at": _now_iso(), "patterns": list(patterns)}
    if backup and paths["learned_patterns"].exists():
        shutil.copy2(paths["learned_patterns"], paths["learned_patterns"].with_suffix(".json.bak"))
    paths["learned_patterns"].write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
    patterns = read_patterns()
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
    if include_events:
        bundle["events_sample"] = events_sample
        bundle["checksums"]["events_sha256"] = _sha256_json(events_sample)
    errors = validate_payload(bundle, schema_filename=TRAINING_BUNDLE_SCHEMA_FILE)
    if errors:
        raise ValueError(f"invalid bundle: {errors[0]}")
    return bundle


def data_show_status() -> str:
    paths = ensure_training_layout()
    profile = read_active_profile()
    patterns = read_patterns()
    events_count = count_event_lines()
    return (
        f"ai-data profile={profile.get('display_name') or 'unknown'} "
        f"patterns={len(patterns)} events={events_count} "
        f"updated={profile.get('updated_at') or '-'} path={paths['active_profile']}"
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
