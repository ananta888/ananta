"""BuildProfile and ArtifactCompatibilityKey helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def profile_digest(profile: dict[str, Any]) -> str:
    body = {key: value for key, value in dict(profile or {}).items() if key != "profile_digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def compatibility_key(*, artifact_kind: str, profile: dict[str, Any]) -> dict[str, Any]:
    embedding = dict(profile.get("embedding_profile") or {})
    graph = dict(profile.get("graph_profile") or {})
    search = dict(profile.get("search_profile") or {})
    relevant = {
        "graph": graph,
        "chunks": {"chunking": (profile.get("canonical_config") or {}).get("chunking")},
        "embeddings": {
            "model": embedding.get("model"),
            "dimensions": embedding.get("dimensions"),
            "embedding_text_profile": embedding.get("embedding_text_profile"),
        },
        "fts": search,
    }.get(artifact_kind, {})
    digest = hashlib.sha256(json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "artifact_kind": artifact_kind,
        "schema_version": "v1",
        "builder_id": f"codecompass.{artifact_kind}",
        "builder_version": "1",
        "relevant_config_digest": digest,
    }


def profiles_share_artifact(left: dict[str, Any], right: dict[str, Any], artifact_kind: str) -> bool:
    return compatibility_key(artifact_kind=artifact_kind, profile=left) == compatibility_key(
        artifact_kind=artifact_kind, profile=right
    )
