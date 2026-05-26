from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_training_data import TRAINING_BUNDLE_SCHEMA_FILE, validate_payload
from client_surfaces.operator_tui.ai_snake_training_store import (
    build_training_bundle,
    read_active_profile,
    read_patterns,
    save_active_profile,
    save_patterns,
)


def export_training_bundle_to_path(
    *,
    output_path: str,
    include_events: bool = False,
) -> Path:
    target = Path(output_path).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_training_bundle(include_events=include_events)
    target.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def export_training_markdown(*, output_path: str, json_ref: str = "") -> Path:
    target = Path(output_path).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    patterns = [item for item in read_patterns() if isinstance(item, dict)]
    active = [item for item in patterns if str(item.get("status") or "") == "active"]
    disabled = [item for item in patterns if str(item.get("status") or "") == "disabled"]
    lines = [
        "# AI-Snake Training Report",
        "",
        f"- Aktive Patterns: {len(active)}",
        f"- Deaktivierte Patterns: {len(disabled)}",
        "",
        "## Datenschutz",
        "",
        "Dieser Report enthält keine privaten Roh-Notes.",
    ]
    if json_ref:
        lines.extend(["", f"JSON-Export: `{json_ref}`"])
    lines.extend(["", "## Patterns", ""])
    for item in active + disabled:
        lines.extend(
            [
                f"- `{item.get('pattern_id')}` [{item.get('status')}]",
                f"  - confidence: {float(item.get('confidence') or 0.0):.2f}",
                f"  - human_explanation: {str(item.get('human_explanation') or '')[:240]}",
                f"  - last_seen_at: {str(item.get('last_seen_at') or '-')}",
            ]
        )
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def preview_training_bundle(path: str) -> dict[str, Any]:
    payload = _read_bundle(path)
    return {
        "schema_version": str(payload.get("schema_version") or ""),
        "profile_name": str(((payload.get("profile") or {}).get("display_name")) or "unknown"),
        "patterns": len(payload.get("patterns") or []),
        "privacy_manifest": dict(payload.get("privacy_manifest") or {}),
    }


def import_training_bundle(
    *,
    input_path: str,
    preview: bool = False,
    disabled: bool = False,
    conflict_strategy: str = "keep_higher_confidence",
) -> dict[str, Any]:
    payload = _read_bundle(input_path)
    schema_version = str(payload.get("schema_version") or "")
    if _is_unknown_future_schema(schema_version):
        return {
            "status": "degraded",
            "readonly": True,
            "reason": "unknown_future_version",
            "schema_version": schema_version,
        }

    errors = validate_payload(payload, schema_filename=TRAINING_BUNDLE_SCHEMA_FILE)
    if errors:
        raise ValueError(f"invalid bundle: {errors[0]}")

    imported_profile = dict(payload.get("profile") or {})
    imported_patterns = [dict(item) for item in (payload.get("patterns") or []) if isinstance(item, dict)]
    if disabled:
        for item in imported_patterns:
            item["status"] = "disabled"

    existing_patterns = read_patterns()
    merged, report = _merge_patterns_with_conflicts(
        existing=existing_patterns,
        incoming=imported_patterns,
        strategy=conflict_strategy,
    )

    result = {
        "status": "preview" if preview else "imported",
        "readonly": bool(preview),
        "schema_version": schema_version,
        "profile_name": str(imported_profile.get("display_name") or "unknown"),
        "patterns_incoming": len(imported_patterns),
        "patterns_result": len(merged),
        "conflicts": report["conflicts"],
        "conflict_resolution": report["resolution"],
        "conflict_examples": report["examples"][:5],
        "privacy_manifest": dict(payload.get("privacy_manifest") or {}),
    }
    if preview:
        return result

    profile_to_save = read_active_profile()
    profile_to_save.update(imported_profile)
    save_active_profile(profile_to_save, backup=True)
    save_patterns(merged, backup=True)
    return result


def _read_bundle(path: str) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("bundle is not an object")
    return payload


def _is_unknown_future_schema(schema_version: str) -> bool:
    value = str(schema_version or "")
    match = re.match(r"^ai_snake_training_bundle\.v(\d+)$", value)
    if not match:
        return False
    return int(match.group(1)) > 1


def _merge_patterns_with_conflicts(
    *,
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    strategy: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    valid_strategy = strategy if strategy in {"keep_higher_confidence", "overwrite", "keep_local", "merge_counters"} else "keep_higher_confidence"
    result_by_id: dict[str, dict[str, Any]] = {}
    fingerprint_index: dict[str, str] = {}
    for item in existing:
        pattern_id = str(item.get("pattern_id") or "")
        if not pattern_id:
            continue
        copied = dict(item)
        result_by_id[pattern_id] = copied
        fingerprint_index[_pattern_fingerprint(copied)] = pattern_id

    conflict_examples: list[str] = []
    conflict_count = 0
    for item in incoming:
        pattern_id = str(item.get("pattern_id") or "")
        if not pattern_id:
            continue
        copied = dict(item)
        target_id = pattern_id
        fingerprint = _pattern_fingerprint(copied)
        if target_id not in result_by_id and fingerprint in fingerprint_index:
            target_id = fingerprint_index[fingerprint]

        if target_id not in result_by_id:
            result_by_id[pattern_id] = copied
            fingerprint_index[fingerprint] = pattern_id
            continue

        conflict_count += 1
        local = dict(result_by_id[target_id])
        remote = copied
        if valid_strategy == "overwrite":
            chosen = remote
        elif valid_strategy == "keep_local":
            chosen = local
        elif valid_strategy == "merge_counters":
            chosen = _merge_counter_fields(local=local, remote=remote)
        else:
            chosen = remote if float(remote.get("confidence") or 0.0) > float(local.get("confidence") or 0.0) else local

        result_by_id[target_id] = chosen
        conflict_examples.append(f"{target_id}:{valid_strategy}")
        fingerprint_index[_pattern_fingerprint(chosen)] = target_id

    return [result_by_id[key] for key in sorted(result_by_id)], {
        "conflicts": conflict_count,
        "resolution": valid_strategy,
        "examples": conflict_examples,
    }


def _merge_counter_fields(*, local: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    merged = dict(local)
    local_counters = dict(local.get("counters") or {})
    remote_counters = dict(remote.get("counters") or {})
    merged["counters"] = {
        "hits": int(local_counters.get("hits") or 0) + int(remote_counters.get("hits") or 0),
        "misses": int(local_counters.get("misses") or 0) + int(remote_counters.get("misses") or 0),
        "positives": int(local_counters.get("positives") or 0) + int(remote_counters.get("positives") or 0),
        "negatives": int(local_counters.get("negatives") or 0) + int(remote_counters.get("negatives") or 0),
    }
    local_ev = dict(local.get("evidence") or {})
    remote_ev = dict(remote.get("evidence") or {})
    source_ids = [str(x) for x in (local_ev.get("source_event_ids") or []) if str(x)] + [
        str(x) for x in (remote_ev.get("source_event_ids") or []) if str(x)
    ]
    dedup = []
    seen: set[str] = set()
    for sid in source_ids:
        if sid in seen:
            continue
        seen.add(sid)
        dedup.append(sid)
    merged["evidence"] = {
        "source_event_ids": dedup[-200:],
        "sample_size": len(dedup),
        "counter_refs": [f"counter:{str(local.get('pattern_id') or remote.get('pattern_id') or 'merged')}"],
    }
    merged["confidence"] = max(float(local.get("confidence") or 0.0), float(remote.get("confidence") or 0.0))
    merged["status"] = str(remote.get("status") or local.get("status") or "draft")
    merged["ai_hint"] = str(remote.get("ai_hint") or local.get("ai_hint") or "")
    merged["human_explanation"] = str(remote.get("human_explanation") or local.get("human_explanation") or "")
    return merged


def _pattern_fingerprint(pattern: dict[str, Any]) -> str:
    key = {
        "predicted_intent": str(pattern.get("predicted_intent") or ""),
        "conditions": pattern.get("conditions"),
    }
    digest = hashlib.sha1(json.dumps(key, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest
