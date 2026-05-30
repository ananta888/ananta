"""SS05.02: Policy- und Redaction-Gate vor View-Share.

- View-Share ist default aus und muss explizit aktiviert werden
- Policy entscheidet pro Snapshot/Delta ob Teilen erlaubt ist
- Sensitive Inhalte (Tokens, Passwörter, Secrets) werden blockiert/redacted
- Notes-Panel wird komplett redacted
- AI-Kontext/Artefaktinhalte nur wenn Session-Rechte es erlauben
- Audit: Hash/Metadaten, kein Screenshot-Klartext
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

# Pattern für sensitive Inhalte
_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwort|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(token|secret|api[_-]?key|apikey|auth[_-]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)Authorization:\s*\S+"),
    re.compile(r"(?i)-----BEGIN [A-Z ]+-----"),
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),  # lange base64-Strings
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),  # lange Hex-Strings (Keys, Hashes)
]

_NOTES_MARKERS = ["[notes]", "notes:", "local only", "notiz:", "private:"]


@dataclass
class PolicyDecision:
    allowed: bool
    redacted: bool = False
    reason: str = ""
    policy_hash: str = ""


@dataclass
class ViewSharePolicy:
    view_share_enabled: bool = False
    allow_ai_context: bool = False
    allow_artifacts: bool = False
    redact_notes: bool = True
    redact_secrets: bool = True


def build_default_policy() -> ViewSharePolicy:
    return ViewSharePolicy()


def build_policy_from_session_permissions(permissions: dict[str, Any]) -> ViewSharePolicy:
    view_tui = bool(permissions.get("view_tui", False))
    artifact_share = bool(permissions.get("artifact_share", False))
    return ViewSharePolicy(
        view_share_enabled=view_tui,
        allow_ai_context=False,
        allow_artifacts=artifact_share,
        redact_notes=True,
        redact_secrets=True,
    )


def apply_redaction(text: str, policy: ViewSharePolicy) -> str:
    """Redacted sensitive Inhalte aus dem Text."""
    if not policy.redact_secrets and not policy.redact_notes:
        return text

    lines = text.splitlines()
    result_lines: list[str] = []
    for line in lines:
        if policy.redact_notes and _is_notes_line(line):
            result_lines.append("[REDACTED: notes]")
            continue
        if policy.redact_secrets and _contains_secret(line):
            result_lines.append("[REDACTED: sensitive]")
            continue
        result_lines.append(line)
    return "\n".join(result_lines)


def check_snapshot(snapshot_text: str, policy: ViewSharePolicy) -> PolicyDecision:
    """Prüft ob ein Snapshot geteilt werden darf und redacted ihn."""
    if not policy.view_share_enabled:
        h = _policy_hash(snapshot_text, policy)
        return PolicyDecision(allowed=False, reason="view_share_disabled", policy_hash=h)

    redacted_text = apply_redaction(snapshot_text, policy)
    was_redacted = redacted_text != snapshot_text
    h = _policy_hash(snapshot_text, policy)
    return PolicyDecision(allowed=True, redacted=was_redacted, policy_hash=h)


def check_and_redact_snapshot(snapshot_text: str, policy: ViewSharePolicy) -> tuple[PolicyDecision, str]:
    decision = check_snapshot(snapshot_text, policy)
    if not decision.allowed:
        return decision, ""
    redacted = apply_redaction(snapshot_text, policy)
    return decision, redacted


def _is_notes_line(line: str) -> bool:
    lower = line.lower()
    return any(marker in lower for marker in _NOTES_MARKERS)


def _contains_secret(line: str) -> bool:
    for pat in _SECRET_PATTERNS:
        if pat.search(line):
            return True
    return False


def _policy_hash(text: str, policy: ViewSharePolicy) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    flags = f"view={policy.view_share_enabled}:notes={policy.redact_notes}:secrets={policy.redact_secrets}"
    return hashlib.sha256(f"{h}:{flags}".encode()).hexdigest()[:16]
