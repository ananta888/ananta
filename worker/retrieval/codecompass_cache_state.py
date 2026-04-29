from __future__ import annotations

from typing import Any

VALID_CHANNELS = ("fts", "vector", "graph")


def build_codecompass_cache_state(
    *,
    manifest_hash: str,
    profile_name: str,
    retrieval_engine_version: str,
    embedding_model_version: str,
    channel_versions: dict[str, str] | None = None,
) -> dict[str, Any]:
    versions = {channel: str((channel_versions or {}).get(channel) or retrieval_engine_version) for channel in VALID_CHANNELS}
    return {
        "schema": "codecompass_cache_state.v1",
        "manifest_hash": str(manifest_hash or "").strip(),
        "profile_name": str(profile_name or "").strip() or "default",
        "retrieval_engine_version": str(retrieval_engine_version or "").strip() or "unknown",
        "embedding_model_version": str(embedding_model_version or "").strip() or "unknown",
        "channels": {channel: {"version": versions[channel]} for channel in VALID_CHANNELS},
    }


def should_invalidate_channel(
    *,
    previous_state: dict[str, Any] | None,
    next_state: dict[str, Any],
    channel: str,
    output_file_deleted: bool = False,
) -> bool:
    normalized_channel = str(channel or "").strip().lower()
    if normalized_channel not in VALID_CHANNELS:
        raise ValueError(f"unknown_codecompass_cache_channel:{normalized_channel or '<missing>'}")
    if output_file_deleted:
        return True
    if not previous_state:
        return True
    previous_manifest = str(previous_state.get("manifest_hash") or "")
    next_manifest = str(next_state.get("manifest_hash") or "")
    if previous_manifest != next_manifest:
        return True
    previous_embedding = str(previous_state.get("embedding_model_version") or "")
    next_embedding = str(next_state.get("embedding_model_version") or "")
    if normalized_channel == "vector" and previous_embedding != next_embedding:
        return True
    previous_channel_version = str(((previous_state.get("channels") or {}).get(normalized_channel) or {}).get("version") or "")
    next_channel_version = str(((next_state.get("channels") or {}).get(normalized_channel) or {}).get("version") or "")
    return previous_channel_version != next_channel_version

