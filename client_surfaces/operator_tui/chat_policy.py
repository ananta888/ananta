"""T06.01 + T06.02 + T06.03: ChatAccessPolicy – Sicherheitsgrenzen für Chat-State.

Actions: write_local, send_hub, send_ai, share_context, export
Default: DENY für alle boundary-crossing Actions.

Policy-Entscheidung liefert: decision, reason_code, decision_ref
Audit-Events enthalten keinen vollständigen Nachrichtentext.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

import re

# ── Sensitive patterns ─────────────────────────────────────────────────────────

_SENSITIVE_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9+/]{40,}\b"),          # long base64-ish tokens
    re.compile(r"(?i)\b(password|passwd|secret|apikey|api_key|token)\s*[:=]\s*\S+"),
    re.compile(r"\b[0-9]{12,16}\b"),                 # long numeric sequences (card-like)
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{20,}\b"),     # API key patterns
    re.compile(r"(?i)\bghp_[A-Za-z0-9]{36,}\b"),    # GitHub PAT
]


def _is_sensitive(text: str) -> bool:
    return any(p.search(text) for p in _SENSITIVE_PATTERNS)


def _redact(text: str) -> str:
    result = text
    for p in _SENSITIVE_PATTERNS:
        result = p.sub("[REDACTED]", result)
    return result


# ── Policy constants ──────────────────────────────────────────────────────────

ACTIONS = ("write_local", "send_hub", "send_ai", "share_context", "export")

# channel_type -> allowed actions (without explicit grant)
_DEFAULT_ALLOW: dict[str, set[str]] = {
    "room": {"write_local", "send_hub"},
    "direct": {"write_local", "send_hub"},
    "ai": {"write_local", "send_ai"},
    "notes": {"write_local"},
    "system": {"write_local"},
}


# ── PolicyDecision ────────────────────────────────────────────────────────────


def make_decision(
    *,
    action: str,
    channel_type: str,
    allowed: bool,
    reason_code: str,
    sender_kind: str = "user",
    target_kind: str = "hub",
    message_hash: str = "",
) -> dict[str, Any]:
    return {
        "decision_ref": str(uuid.uuid4()),
        "action": action,
        "channel_type": channel_type,
        "sender_kind": sender_kind,
        "target_kind": target_kind,
        "decision": "allow" if allowed else "deny",
        "reason_code": reason_code,
        "message_hash": message_hash,
        "ts": time.time(),
    }


# ── Main policy check ─────────────────────────────────────────────────────────


def check_policy(
    msg: dict[str, Any],
    action: str,
    *,
    notes_context_released: bool = False,
    is_external_ai: bool = False,
) -> dict[str, Any]:
    """Evaluate whether `action` is permitted for `msg`.

    Returns a policy decision dict (decision, reason_code, decision_ref).
    """
    channel_type = str(msg.get("channel_type") or "room")
    text = str(msg.get("text") or "")
    sender_kind = str(msg.get("sender_kind") or "user")
    msg_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

    allowed_for_type = _DEFAULT_ALLOW.get(channel_type, set())

    # external AI cannot receive notes under any circumstance
    if is_external_ai and channel_type == "notes":
        return make_decision(
            action=action, channel_type=channel_type, allowed=False,
            reason_code="external_ai_notes_denied", sender_kind=sender_kind,
            target_kind="external_ai", message_hash=msg_hash,
        )

    # notes are ALWAYS local_only (can never be sent to hub or exported)
    if channel_type == "notes" and action not in {"write_local", "send_ai"}:
        return make_decision(
            action=action, channel_type=channel_type, allowed=False,
            reason_code="notes_local_only", sender_kind=sender_kind,
            target_kind="hub", message_hash=msg_hash,
        )

    # AI context for notes requires explicit release
    if channel_type == "notes" and action == "send_ai":
        if not notes_context_released:
            return make_decision(
                action=action, channel_type=channel_type, allowed=False,
                reason_code="notes_context_not_released", sender_kind=sender_kind,
                target_kind="ai", message_hash=msg_hash,
            )

    # sensitive content check for boundary-crossing actions
    if action in {"send_hub", "send_ai", "share_context", "export"}:
        if _is_sensitive(text):
            return make_decision(
                action=action, channel_type=channel_type, allowed=False,
                reason_code="sensitive_content_blocked", sender_kind=sender_kind,
                target_kind="hub" if action == "send_hub" else "ai", message_hash=msg_hash,
            )

    if action not in allowed_for_type:
        return make_decision(
            action=action, channel_type=channel_type, allowed=False,
            reason_code="action_not_permitted_for_channel_type", sender_kind=sender_kind,
            target_kind="hub", message_hash=msg_hash,
        )

    return make_decision(
        action=action, channel_type=channel_type, allowed=True,
        reason_code="allowed", sender_kind=sender_kind,
        target_kind="hub" if action == "send_hub" else "ai", message_hash=msg_hash,
    )


def check_and_redact(msg: dict[str, Any], action: str, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check policy and return (possibly redacted message, decision).

    The original message is never mutated.
    """
    decision = check_policy(msg, action, **kwargs)
    if decision["decision"] == "allow":
        text = str(msg.get("text") or "")
        redacted = _redact(text)
        if redacted != text:
            msg = dict(msg)
            msg["text"] = redacted
    return msg, decision


# ── Audit log ─────────────────────────────────────────────────────────────────

_AUDIT_LOG: list[dict[str, Any]] = []
_MAX_AUDIT = 500


def audit(decision: dict[str, Any]) -> None:
    """Append policy decision to in-memory audit log. No message text stored."""
    entry = {k: v for k, v in decision.items() if k != "text"}
    _AUDIT_LOG.append(entry)
    if len(_AUDIT_LOG) > _MAX_AUDIT:
        del _AUDIT_LOG[:-_MAX_AUDIT]


def get_audit_log() -> list[dict[str, Any]]:
    return list(_AUDIT_LOG)


# ── System message helper ─────────────────────────────────────────────────────

def system_message_for_deny(decision: dict[str, Any]) -> str:
    reason = str(decision.get("reason_code") or "policy_deny")
    action = str(decision.get("action") or "?")
    return f"* [system] policy deny: {action} → {reason}"


def chat_decision_to_decision_result(decision: dict[str, Any]) -> Any:
    """Adapter: konvertiert chat_policy decision-dict zu DecisionResult."""
    from agent.services.heuristic_runtime.decision_result import DecisionResult
    allowed = str(decision.get("decision") or "deny") == "allow"
    reason_code = str(decision.get("reason_code") or "")
    action = str(decision.get("action") or "no_action")
    if not allowed:
        return DecisionResult.policy_denied(reason_code or "policy_blocked")
    action_kind = "send" if action in {"send_hub", "send_ai"} else action
    return DecisionResult(
        action_kind=action_kind,
        confidence=1.0,
        source="heuristic",
        reason_codes=[reason_code] if reason_code and reason_code != "allowed" else [],
    )


# ── HallucinationGuardrail ────────────────────────────────────────────────────

from dataclasses import dataclass, field as _field


@dataclass
class GuardrailResult:
    passed: bool
    reason_codes: list[str] = _field(default_factory=list)
    blocked_refs: list[str] = _field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "reason_codes": list(self.reason_codes),
                "blocked_refs": list(self.blocked_refs)}


def validate_heuristic_answer(answer_block: Any, allowed_refs: list[str]) -> GuardrailResult:
    """Validate a ChatAnswerBlock against the allowed context refs.

    Rules:
      1. All source_refs must come from allowed_refs — no invented filenames/symbols.
      2. If no refs (no_good_match), result_text must not contain concrete file/symbol
         patterns (path-like or PascalCase symbols with dots).
      3. Sensitive patterns in result_text → block.
    """
    import re

    reason_codes: list[str] = []
    blocked_refs: list[str] = []

    source_refs = [r.get("ref") if isinstance(r, dict) else getattr(r, "ref", str(r))
                   for r in (getattr(answer_block, "source_refs", None) or [])]

    allowed_set = set(allowed_refs)

    # Rule 1 — all source_refs must be in allowed_refs
    for ref in source_refs:
        if ref not in allowed_set:
            reason_codes.append(f"hallucinated_ref:{ref}")
            blocked_refs.append(ref)

    # Rule 2 — no_good_match: result_text must not contain concrete refs
    result_text = str(getattr(answer_block, "result_text", "") or "")
    if not source_refs:
        _PATH_RE = re.compile(r"[a-zA-Z0-9_/\\]+\.[a-zA-Z]{2,5}")
        _SYMBOL_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]+\.[a-zA-Z]+\b")
        if _PATH_RE.search(result_text) or _SYMBOL_RE.search(result_text):
            reason_codes.append("no_good_match_but_concrete_ref_in_text")

    # Rule 3 — sensitive content in result_text
    if _is_sensitive(result_text):
        reason_codes.append("sensitive_content_in_result_text")
        result_text = _redact(result_text)

    # Uncertainty note: check if 0 < confidence < 0.7 has uncertainty_note
    # confidence=0.0 is the explicit no_good_match signal, not a low-quality answer
    confidence = float(getattr(answer_block, "confidence", 1.0))
    uncertainty_note = str(getattr(answer_block, "uncertainty_note", "") or "")
    if 0.0 < confidence < 0.7 and not uncertainty_note:
        reason_codes.append("low_confidence_without_uncertainty_note")

    passed = len(reason_codes) == 0 and len(blocked_refs) == 0
    return GuardrailResult(passed=passed, reason_codes=reason_codes, blocked_refs=blocked_refs)
