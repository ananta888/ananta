"""T04.02 + T04.03: AI-Snake-Kontext – sichtbar, steuerbar und policy-gesteuert.

AI-Kontext-Modell:
  allowed_context_refs   – Quellen, die der AI erlaubt sind
  denied_context_refs    – Quellen, die explizit blockiert sind
  sensitivity            – none | low | medium | high
  policy_decision_ref    – Ref aus ChatAccessPolicy
  active_artifact_ref    – aktuell selektiertes Artefakt (T04.03)
  notes_released         – ob Notes temporär freigegeben sind
"""
from __future__ import annotations

import json
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from client_surfaces.operator_tui.ai_snake_training_store import read_active_profile, read_patterns


def default_ai_context() -> dict[str, Any]:
    return {
        "allowed_context_refs": ["artifact", "local_knowledge"],
        "denied_context_refs": ["notes"],
        "sensitivity": "none",
        "policy_decision_ref": None,
        "active_artifact_ref": None,
        "notes_released": False,
        "context_sources_display": "",
    }


def get_ai_context(game: dict[str, Any]) -> dict[str, Any]:
    raw = game.get("ai_snake_context")
    if isinstance(raw, dict):
        return raw
    return default_ai_context()


def set_ai_context(game: dict[str, Any], ctx: dict[str, Any]) -> None:
    game["ai_snake_context"] = ctx


def set_active_artifact(ctx: dict[str, Any], artifact_ref: dict[str, Any] | None) -> None:
    ctx["active_artifact_ref"] = artifact_ref
    _update_display(ctx)


def release_notes_context(ctx: dict[str, Any], *, released: bool) -> None:
    ctx["notes_released"] = released
    denied = list(ctx.get("denied_context_refs") or [])
    allowed = list(ctx.get("allowed_context_refs") or [])
    if released:
        if "notes" in denied:
            denied.remove("notes")
        if "notes" not in allowed:
            allowed.append("notes")
    else:
        if "notes" in allowed:
            allowed.remove("notes")
        if "notes" not in denied:
            denied.append("notes")
    ctx["denied_context_refs"] = denied
    ctx["allowed_context_refs"] = allowed
    _update_display(ctx)


def build_context_payload(
    ctx: dict[str, Any],
    *,
    question: str,
    chat_history: list[dict[str, Any]] | None = None,
    artifact_text: str | None = None,
    notes_text: str | None = None,
) -> dict[str, Any]:
    """Assemble the payload passed to AI. Never includes sources not in allowed_context_refs."""
    allowed = set(ctx.get("allowed_context_refs") or [])
    payload: dict[str, Any] = {"question": question, "sources": []}

    if "artifact" in allowed and artifact_text:
        payload["artifact_context"] = artifact_text[:2000]
        payload["sources"].append("artifact")

    if "notes" in allowed and notes_text and ctx.get("notes_released"):
        payload["notes_context"] = notes_text[:1000]
        payload["sources"].append("notes")

    if chat_history and "local_knowledge" in allowed:
        payload["recent_chat"] = chat_history[-6:]
        payload["sources"].append("local_knowledge")

    return payload


def _update_display(ctx: dict[str, Any]) -> None:
    allowed = list(ctx.get("allowed_context_refs") or [])
    parts = []
    if "artifact" in allowed and ctx.get("active_artifact_ref"):
        parts.append("artifact")
    if "notes" in allowed and ctx.get("notes_released"):
        parts.append("notes")
    if "local_knowledge" in allowed:
        parts.append("local")
    ctx["context_sources_display"] = "+".join(parts) if parts else "none"


def artifact_ref_from_game(game: dict[str, Any]) -> dict[str, Any] | None:
    """Extract current artifact selection from game state (for T04.03)."""
    intent_target = game.get("artifact_intent_target")
    if isinstance(intent_target, dict) and intent_target:
        return intent_target
    chat_raw = game.get("artifact_chat_state")
    if isinstance(chat_raw, dict):
        active = chat_raw.get("active_target")
        if isinstance(active, dict) and active:
            return active
    return None


def is_policy_blocked(ctx: dict[str, Any], source: str) -> bool:
    return source in (ctx.get("denied_context_refs") or [])


def load_codecompass_artifact(
    path: str | Path = "artifacts/codecompass/operator_tui_snake_context.json",
) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def build_context_envelope_ref(
    ctx: dict[str, Any],
    *,
    codecompass_artifact: dict[str, Any] | None,
    selected_artifact_ref: dict[str, Any] | None = None,
    artifact_grant_refs: list[str] | None = None,
    source_usage_refs: list[str] | None = None,
    denied_context_refs: list[str] | None = None,
) -> dict[str, Any]:
    degraded = codecompass_artifact is None
    refs = []
    if isinstance(selected_artifact_ref, dict) and selected_artifact_ref:
        refs.append(
            {
                "ref_type": "selected_artifact",
                "ref": str(selected_artifact_ref.get("path") or selected_artifact_ref.get("label") or "artifact"),
                "reason": "active selection",
                "score": 1.0,
            }
        )
    if isinstance(codecompass_artifact, dict):
        for item in list(codecompass_artifact.get("refs") or [])[:12]:
            if not isinstance(item, dict):
                continue
            refs.append(
                {
                    "ref_type": "codecompass",
                    "ref": str(item.get("ref") or ""),
                    "reason": str(item.get("reason") or "context"),
                    "score": float(item.get("score") or 0.5),
                }
            )
    grant_refs = [str(item) for item in list(artifact_grant_refs or []) if str(item).strip()]
    usage_refs = [str(item) for item in list(source_usage_refs or []) if str(item).strip()]
    denied_refs = [str(item) for item in list(denied_context_refs or []) if str(item).strip()]
    context_hash = str((codecompass_artifact or {}).get("context_hash") or "missing")
    if grant_refs or usage_refs:
        seed = "|".join([context_hash, ",".join(sorted(grant_refs)), ",".join(sorted(usage_refs))])
        context_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

    return {
        "context_bundle_id": "operator_tui_snake_context",
        "context_hash": context_hash,
        "retrieval_refs": refs[:12],
        "sensitivity": str(ctx.get("sensitivity") or "none"),
        "degraded_state": "missing_artifact" if degraded else "",
        "artifact_grant_refs": grant_refs,
        "source_usage_refs": usage_refs,
        "denied_context_refs": denied_refs,
    }


def training_profile_envelope(
    *,
    intent: str,
    max_patterns: int = 8,
) -> dict[str, Any]:
    profile = read_active_profile()
    patterns = [item for item in read_patterns() if isinstance(item, dict)]
    active = [
        item
        for item in patterns
        if str(item.get("status") or "") == "active" and not _is_expired(item.get("expires_at"))
    ]
    intent_key = str(intent or "").strip().lower()

    def _score(item: dict[str, Any]) -> tuple[float, float]:
        base = float(item.get("confidence") or 0.0)
        bonus = 0.0
        if intent_key and str(item.get("predicted_intent") or "").strip().lower() == intent_key:
            bonus += 0.20
        recency = _recency_score(item.get("last_seen_at"))
        return (base + bonus + recency, base)

    ranked = sorted(active, key=_score, reverse=True)[: max(1, int(max_patterns))]
    selected = [
        {
            "pattern_id": str(item.get("pattern_id") or ""),
            "predicted_intent": str(item.get("predicted_intent") or "unknown"),
            "confidence": round(float(item.get("confidence") or 0.0), 3),
            "ai_hint": str(item.get("ai_hint") or "")[:300],
            "status": str(item.get("status") or "active"),
            "last_seen_at": str(item.get("last_seen_at") or ""),
        }
        for item in ranked
        if str(item.get("pattern_id") or "")
    ]
    return {
        "training_profile_ref": {
            "profile_id": str(profile.get("profile_id") or "default"),
            "display_name": str(profile.get("display_name") or "unknown"),
            "ai_summary": str(profile.get("ai_summary") or ""),
            "workspace_ref": str(profile.get("workspace_ref") or "local"),
        },
        "active_pattern_refs": selected,
    }


def relevance_refs_for_intent(
    *,
    intent: str,
    codecompass_artifact: dict[str, Any] | None,
    max_refs: int = 12,
) -> list[dict[str, Any]]:
    refs = list((codecompass_artifact or {}).get("refs") or [])
    if not refs:
        return [
            {
                "ref": "client_surfaces/operator_tui/interactive.py",
                "reason": "fallback runtime context",
                "score": 0.4,
            }
        ]
    intent_key = str(intent or "unknown").lower()
    ranked: list[dict[str, Any]] = []
    for item in refs:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or "")
        reason = str(item.get("reason") or "context")
        score = float(item.get("score") or 0.5)
        lowered = f"{ref} {reason}".lower()
        if intent_key == "artifact_explain" and ("artifact" in lowered or "renderer" in lowered):
            score += 0.2
        if intent_key == "chat" and ("chat" in lowered or "transport" in lowered):
            score += 0.2
        if intent_key == "notes" and ("notes" in lowered or "policy" in lowered):
            score += 0.2
        ranked.append({"ref": ref, "reason": reason, "score": round(score, 3)})
    ranked.sort(key=lambda row: row["score"], reverse=True)
    return ranked[: max(1, min(20, int(max_refs)))]


def compact_observation_summary(summary: dict[str, Any], *, max_facts: int = 20) -> dict[str, Any]:
    """Stable prompt-safe summary with prioritized fields and no raw notes."""
    facts = [str(item).strip() for item in list(summary.get("facts") or []) if str(item).strip()]
    sections = [item for item in facts if item.startswith("section=")]
    channels = [item for item in facts if item.startswith("channel=")]
    refs = [item for item in facts if item.startswith("selected_ref=")]
    commands = [item for item in facts if item.startswith("last_command=")]
    movement = [item for item in facts if item.startswith("movement_trend=")]
    other = [item for item in facts if item not in sections + channels + refs + commands + movement]
    ordered = sections[:1] + channels[:1] + refs[:1] + commands[:1] + movement[:1] + other
    clipped = ordered[: max(4, int(max_facts))]
    return {
        "facts": clipped,
        "notes_active": bool(summary.get("notes_active")),
        "event_count": int(summary.get("event_count") or 0),
    }


def _is_expired(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt <= datetime.now(UTC)


def _recency_score(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    delta = max(0.0, (datetime.now(UTC) - dt).total_seconds())
    if delta <= 3600:
        return 0.10
    if delta <= 86400:
        return 0.05
    return 0.0
