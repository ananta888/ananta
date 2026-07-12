from __future__ import annotations


def voice_retention_policy(*, categories: list[str], retention_days: int | None) -> dict[str, dict]:
    granted = set(categories)
    bounded_days = max(1, min(int(retention_days or 1), 3650))
    return {
        "raw_audio": {
            "purpose": "ephemeral_transcription",
            "persist": False,
            "retention_days": 0,
        },
        "audio_excerpt": {
            "purpose": "review_context",
            "persist": False,
            "retention_days": 0,
        },
        "keyed_fingerprint": {
            "purpose": "tenant_idempotency",
            "persist": "audio_fingerprint" in granted,
            "retention_days": min(bounded_days, 30) if "audio_fingerprint" in granted else 0,
        },
        "idempotency_audio_binding": {
            "purpose": "single_key_replay_conflict_detection",
            "persist": True,
            "cross_request_linkable": False,
            "retention_days": 1,
        },
        "feedback": {
            "purpose": "profile_personalization",
            "persist": bool(granted & {"preferences", "text_corrections", "vocabulary"}),
            "retention_days": bounded_days,
        },
        "derived_rules": {
            "purpose": "deterministic_profile_rules",
            "persist": bool(granted & {"preferences", "text_corrections", "vocabulary"}),
            "retention_days": bounded_days,
        },
        "transcript_result": {
            "purpose": "immutable_review_reference",
            "persist": True,
            "retention_days": min(bounded_days, 30),
        },
    }
