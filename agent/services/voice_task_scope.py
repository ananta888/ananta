from __future__ import annotations

import hashlib
from collections.abc import Mapping


def inherited_voice_task_scope(parent_task_id: str, *, tenant_id: str) -> dict[str, str]:
    """Copy only verified opaque privacy scope from a Hub-owned parent task."""

    normalized_parent = str(parent_task_id or "").strip()
    if not normalized_parent:
        return {}
    from agent.repository import task_repo

    parent = task_repo.get_by_id(normalized_parent)
    raw_context = getattr(parent, "worker_execution_context", None) if parent is not None else None
    context = raw_context if isinstance(raw_context, Mapping) else {}
    raw_voice = context.get("voice_transcription")
    voice = raw_voice if isinstance(raw_voice, Mapping) else {}
    expected_tenant_hash = hashlib.sha256(tenant_id.encode()).hexdigest()
    if voice.get("tenant_scope_hash") != expected_tenant_hash:
        return {}
    owner_subject_hash = str(voice.get("owner_subject_hash") or "").strip()
    profile_id = str(voice.get("profile_id") or "").strip()
    deletion_scope_digest = str(voice.get("deletion_scope_digest") or "").strip()
    if len(owner_subject_hash) != 64 or not profile_id:
        return {}
    inherited = {
        "owner_subject_hash": owner_subject_hash,
        "profile_id": profile_id,
    }
    if len(deletion_scope_digest) == 64:
        inherited["deletion_scope_digest"] = deletion_scope_digest
    return inherited
