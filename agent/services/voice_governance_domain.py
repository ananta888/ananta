"""Compatibility re-export of the dependency-free Voice governance domain.

The canonical home is :mod:`agent.models.voice_governance_domain` so that
repositories can depend on the domain types without importing the service
layer. Existing ``agent.services.voice_governance_domain`` imports keep
resolving to the very same objects.
"""

from __future__ import annotations

from agent.models.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
    stable_payload_hash,
    validate_identifier,
    validate_text,
    voice_deletion_ledger_signature,
    voice_idempotency_audio_binding,
    voice_idempotency_key_digest,
    voice_idempotency_storage_key,
    voice_scope_digest,
)

__all__ = [
    "VoiceGovernanceError",
    "VoicePrincipal",
    "stable_payload_hash",
    "validate_identifier",
    "validate_text",
    "voice_deletion_ledger_signature",
    "voice_idempotency_audio_binding",
    "voice_idempotency_key_digest",
    "voice_idempotency_storage_key",
    "voice_scope_digest",
]
