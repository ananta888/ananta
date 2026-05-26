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

from typing import Any


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
