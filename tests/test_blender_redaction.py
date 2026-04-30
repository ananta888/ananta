from __future__ import annotations

from agent.services.blender_redaction_service import redact_blender_payload


def test_blender_redaction_masks_secrets():
    payload={"token":"abc","nested":{"api_key":"123","text":"password=foo"}}
    out=redact_blender_payload(payload)
    assert out["token"]=="[REDACTED]"
    assert out["nested"]["api_key"]=="[REDACTED]"
    assert "[REDACTED]" in out["nested"]["text"]
