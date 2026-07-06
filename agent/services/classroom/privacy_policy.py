"""CTA-012: PII-Redaction, Retention und Audit-Action-Namen.

Reine Funktionen, damit die Policy isoliert testbar bleibt. Der Audit
laeuft ueber den bestehenden log_audit-Pfad (agent/common/audit.py);
dieses Modul definiert nur die Action-Namen und die Redaction-Regeln.
"""

from __future__ import annotations

import hashlib
import re
import time

AUDIT_EVENT_RECEIVED = "classroom_event_received"
AUDIT_CARD_CREATED = "classroom_card_created"
AUDIT_ANSWER_PROPOSED = "classroom_answer_proposed"
AUDIT_WORKFLOW_PROPOSED = "classroom_workflow_proposed"
AUDIT_WORKFLOW_EXPORTED = "classroom_workflow_exported"

AUDIT_ACTIONS = (
    AUDIT_EVENT_RECEIVED,
    AUDIT_CARD_CREATED,
    AUDIT_ANSWER_PROPOSED,
    AUDIT_WORKFLOW_PROPOSED,
    AUDIT_WORKFLOW_EXPORTED,
)

DEFAULT_RAW_SEGMENT_RETENTION_HOURS = 72

_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\s/()-]{6,}\d)")
# Zwei aufeinanderfolgende kapitalisierte Woerter = Namens-Kandidat.
# Bewusst ueberredigierend: im Unterrichts-Transkript ist ein falsch
# redigiertes Hauptwort billiger als ein geleakter Schuelername.
_FULL_NAME_PATTERN = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]{2,}\s+[A-ZÄÖÜ][a-zäöüß]{2,}\b")
_SPEAKER_HASH_PATTERN = re.compile(r"^spk-[0-9a-f]{12}$")

REDACTED_NAME = "<name>"
REDACTED_CONTACT = "<contact>"


def hash_speaker_label(label: str) -> str:
    """Stabiler Alias fuer einen Sprecher; Klarname wird nie persistiert."""
    normalized = str(label or "unknown").strip().lower()
    return "spk-" + hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()[:12]


def is_valid_speaker_hash(value: str) -> bool:
    return bool(_SPEAKER_HASH_PATTERN.fullmatch(str(value or "").strip()))


def redact_pii(text: str) -> tuple[str, int]:
    """Entfernt E-Mails, Telefonnummern und Namens-Kandidaten.

    Rueckgabe: (redigierter Text, Anzahl Redactions).
    """
    value = str(text or "")
    count = 0

    def _sub(pattern: re.Pattern, replacement: str, current: str) -> str:
        nonlocal count
        current, n = pattern.subn(replacement, current)
        count += n
        return current

    value = _sub(_EMAIL_PATTERN, REDACTED_CONTACT, value)
    value = _sub(_PHONE_PATTERN, REDACTED_CONTACT, value)
    value = _sub(_FULL_NAME_PATTERN, REDACTED_NAME, value)
    return value, count


def retention_hours(cfg: dict | None) -> int:
    classroom_cfg = (cfg or {}).get("classroom") if isinstance((cfg or {}).get("classroom"), dict) else {}
    try:
        hours = int(classroom_cfg.get("retention_hours_raw_segments", DEFAULT_RAW_SEGMENT_RETENTION_HOURS))
    except (TypeError, ValueError):
        hours = DEFAULT_RAW_SEGMENT_RETENTION_HOURS
    return max(1, min(hours, 24 * 90))


def prune_expired_segments(segments: list[dict], *, cfg: dict | None = None, now: float | None = None) -> list[dict]:
    """Entfernt Roh-Segmente nach TTL. Karten sind davon nicht betroffen."""
    ttl_seconds = retention_hours(cfg) * 3600
    current = now if now is not None else time.time()

    def _received_at(segment: dict) -> float:
        raw = segment.get("received_at")
        return current if raw is None else float(raw)

    return [seg for seg in segments if (current - _received_at(seg)) < ttl_seconds]
