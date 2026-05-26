from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_training_data import validate_learned_pattern


def mine_patterns_from_events(
    *,
    events: list[dict[str, Any]],
    min_cases: int = 3,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    timestamp = now or datetime.now(UTC)
    minimum = max(1, int(min_cases))
    section_by_ref: dict[str, str] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for item in events:
        event_type, value, refs = _normalize_event(item)
        if event_type == "section_change":
            section_ref = str(refs.get("section_ref") or "")
            if section_ref.startswith("section:"):
                section_by_ref[section_ref] = value or section_ref.removeprefix("section:")
        if event_type != "artifact_selected":
            continue
        artifact_ref = str(refs.get("artifact_ref") or value or "")
        section_ref = str(refs.get("section_ref") or "")
        section = section_by_ref.get(section_ref, section_ref.removeprefix("section:") or "dashboard")
        grouped.setdefault((section, artifact_ref), []).append(item)

    patterns: list[dict[str, Any]] = []
    for (section, artifact_ref), sample in sorted(grouped.items()):
        if len(sample) < minimum:
            continue
        pattern = _build_sequence_pattern(
            section=section,
            artifact_ref=artifact_ref,
            evidence=sample,
            min_cases=minimum,
            now=timestamp,
        )
        errors = validate_learned_pattern(pattern)
        if errors:
            continue
        patterns.append(pattern)
    return patterns


def merge_patterns(
    *,
    existing: list[dict[str, Any]],
    mined: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {
        str(item.get("pattern_id") or ""): dict(item)
        for item in existing
        if isinstance(item, dict) and str(item.get("pattern_id") or "")
    }
    for pattern in mined:
        pattern_id = str(pattern.get("pattern_id") or "")
        if not pattern_id:
            continue
        if pattern_id not in by_id:
            by_id[pattern_id] = dict(pattern)
            continue
        current = by_id[pattern_id]
        current_counters = dict(current.get("counters") or {})
        mined_counters = dict(pattern.get("counters") or {})
        merged_hits = int(current_counters.get("hits") or 0) + int(mined_counters.get("hits") or 0)
        merged_misses = int(current_counters.get("misses") or 0)
        merged_pos = int(current_counters.get("positives") or 0) + int(mined_counters.get("positives") or 0)
        merged_neg = int(current_counters.get("negatives") or 0)
        current["counters"] = {
            "hits": merged_hits,
            "misses": merged_misses,
            "positives": merged_pos,
            "negatives": merged_neg,
        }
        current["confidence"] = _confidence_from_counts(
            positives=merged_pos,
            negatives=merged_neg,
            hits=merged_hits,
            min_cases=max(1, int((current.get("evidence") or {}).get("sample_size") or 3)),
        )
        current["status"] = "active" if float(current.get("confidence") or 0.0) >= 0.55 else "draft"
        current["last_seen_at"] = str(pattern.get("last_seen_at") or current.get("last_seen_at") or _iso_now())
        source_ids = list((current.get("evidence") or {}).get("source_event_ids") or [])
        source_ids.extend(list((pattern.get("evidence") or {}).get("source_event_ids") or []))
        dedup_ids = []
        seen: set[str] = set()
        for sid in source_ids:
            text = str(sid)
            if text and text not in seen:
                seen.add(text)
                dedup_ids.append(text)
        sample_size = len(dedup_ids)
        current["evidence"] = {
            "source_event_ids": dedup_ids[-200:],
            "sample_size": sample_size,
            "counter_refs": [f"counter:{pattern_id}"],
        }
        by_id[pattern_id] = current
    return [by_id[key] for key in sorted(by_id)]


def apply_prediction_feedback(
    *,
    patterns: list[dict[str, Any]],
    target_ref: str,
    positive: bool,
) -> tuple[list[dict[str, Any]], bool]:
    key = str(target_ref or "").strip()
    if not key:
        return patterns, False
    changed = False
    updated: list[dict[str, Any]] = []
    for item in patterns:
        next_item = dict(item)
        if str(next_item.get("predicted_intent") or "") != "artifact_explain":
            updated.append(next_item)
            continue
        conditions = next_item.get("conditions")
        if not _conditions_match_target(conditions=conditions, target_ref=key):
            updated.append(next_item)
            continue
        counters = dict(next_item.get("counters") or {})
        if positive:
            counters["positives"] = int(counters.get("positives") or 0) + 1
            counters["hits"] = int(counters.get("hits") or 0) + 1
        else:
            counters["negatives"] = int(counters.get("negatives") or 0) + 1
            counters["misses"] = int(counters.get("misses") or 0) + 1
        next_item["counters"] = {
            "hits": int(counters.get("hits") or 0),
            "misses": int(counters.get("misses") or 0),
            "positives": int(counters.get("positives") or 0),
            "negatives": int(counters.get("negatives") or 0),
        }
        evidence = dict(next_item.get("evidence") or {})
        sample_size = max(
            int(evidence.get("sample_size") or 0),
            int(next_item["counters"]["hits"]) + int(next_item["counters"]["misses"]),
        )
        evidence["sample_size"] = sample_size
        next_item["evidence"] = evidence
        next_item["confidence"] = _confidence_from_counts(
            positives=int(next_item["counters"]["positives"]),
            negatives=int(next_item["counters"]["negatives"]),
            hits=int(next_item["counters"]["hits"]),
            min_cases=max(1, sample_size if sample_size < 6 else 3),
        )
        next_item["status"] = "active" if float(next_item["confidence"]) >= 0.55 else "draft"
        next_item["last_seen_at"] = _iso_now()
        changed = True
        updated.append(next_item)
    return updated, changed


def event_for_prediction_feedback(*, target_ref: str, positive: bool, reason: str = "") -> dict[str, Any]:
    return {
        "event_type": "prediction_feedback",
        "value_norm": "good" if positive else "bad",
        "refs": [str(target_ref or "").strip()],
        "privacy_class": "workspace",
        "retention_hint": "rolling_30d",
        "reason": str(reason or "")[:160],
    }


def compact_event_log(
    *,
    events_path: Path,
    max_bytes: int,
    keep_last_lines: int = 500,
    backup: bool = True,
) -> dict[str, int]:
    path = Path(events_path)
    if not path.exists():
        return {"before_bytes": 0, "after_bytes": 0, "kept_lines": 0}
    before = int(path.stat().st_size)
    if backup:
        path.with_suffix(path.suffix + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    kept = lines[-max(1, int(keep_last_lines)) :]
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    after = int(path.stat().st_size)
    if after > int(max_bytes):
        encoded = "\n".join(kept).encode("utf-8")
        slice_size = max(1, min(len(encoded), max(1, int(max_bytes) - 1)))
        tail = encoded[-slice_size:]
        path.write_bytes(tail if tail.endswith(b"\n") else tail + b"\n")
        after = int(path.stat().st_size)
    return {"before_bytes": before, "after_bytes": after, "kept_lines": len(kept)}


def _normalize_event(item: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    event_type_raw = str(item.get("event_type") or "")
    event_type = {
        "section_visit": "section_change",
        "artifact_focus": "artifact_selected",
        "movement_vector": "movement",
    }.get(event_type_raw, event_type_raw)
    value = str(item.get("normalized_value") or item.get("value_norm") or "")
    refs_raw = item.get("refs")
    refs: dict[str, str] = {}
    if isinstance(refs_raw, dict):
        refs = {str(k): str(v) for k, v in refs_raw.items() if str(v)}
    elif isinstance(refs_raw, list):
        for ref in refs_raw:
            text = str(ref)
            if text.startswith("section:"):
                refs["section_ref"] = text
            elif text:
                refs["artifact_ref"] = text
    return event_type, value, refs


def _build_sequence_pattern(
    *,
    section: str,
    artifact_ref: str,
    evidence: list[dict[str, Any]],
    min_cases: int,
    now: datetime,
) -> dict[str, Any]:
    source_ids = [str(item.get("event_id") or "") for item in evidence if str(item.get("event_id") or "")]
    sample_size = len(source_ids)
    confidence = _confidence_from_counts(
        positives=sample_size,
        negatives=0,
        hits=sample_size,
        min_cases=max(1, int(min_cases)),
    )
    status = "active" if confidence >= 0.55 else "draft"
    target = f"artifact:{artifact_ref}" if artifact_ref else "artifact:unknown"
    stable = hashlib.sha1(f"{section}|{artifact_ref}".encode("utf-8")).hexdigest()[:10]
    pattern_id = f"pat-artifact-{stable}"
    return {
        "pattern_id": pattern_id,
        "pattern_type": "sequence_rule",
        "conditions": {
            "all": [
                {"field": "section_is", "op": "eq", "value": section or "dashboard"},
                {"field": "selected_ref_kind", "op": "eq", "value": "artifact"},
                {"field": "recent_event", "op": "contains", "value": target},
            ]
        },
        "predicted_intent": "artifact_explain",
        "confidence": confidence,
        "evidence": {
            "source_event_ids": source_ids[-200:],
            "sample_size": sample_size,
            "counter_refs": [f"counter:{pattern_id}"],
        },
        "counters": {
            "hits": sample_size,
            "misses": 0,
            "positives": sample_size,
            "negatives": 0,
        },
        "last_seen_at": _iso_now(now),
        "expires_at": _iso_now(now + timedelta(days=30)),
        "status": status,
        "human_explanation": (
            f"Wenn in Abschnitt '{section or 'dashboard'}' mehrfach dasselbe Artefakt fokussiert wird, "
            "ist eine Erklärungsabsicht wahrscheinlich."
        ),
        "ai_hint": f"Wenn Target {target} erneut auftaucht, kurz erklären statt nur zu folgen.",
    }


def _conditions_match_target(*, conditions: Any, target_ref: str) -> bool:
    if isinstance(conditions, dict):
        if "value" in conditions and str(conditions.get("value") or "") == f"artifact:{target_ref}":
            return True
        for key in ("all", "any"):
            items = conditions.get(key)
            if isinstance(items, list):
                for child in items:
                    if _conditions_match_target(conditions=child, target_ref=target_ref):
                        return True
        if "not" in conditions:
            return _conditions_match_target(conditions=conditions.get("not"), target_ref=target_ref)
    return False


def _confidence_from_counts(*, positives: int, negatives: int, hits: int, min_cases: int) -> float:
    total_feedback = max(0, int(positives)) + max(0, int(negatives))
    quality = (max(0, int(positives)) + 1) / (total_feedback + 2)
    support = min(1.0, max(0, int(hits)) / max(1, int(min_cases) * 2))
    return round(max(0.05, min(0.95, (0.35 * quality) + (0.60 * support))), 3)


def _iso_now(ts: datetime | None = None) -> str:
    timestamp = ts or datetime.now(UTC)
    return timestamp.isoformat().replace("+00:00", "Z")
