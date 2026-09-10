"""Immutable Hub-local child snapshot; no policy grant or evidence identity."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agent.services.native_context_chunks import native_context_chunk_fields
from ananta_contracts.native_context_bundle import MAX_NATIVE_CONTEXT_BYTES, native_context_digest


def bounded_context_identifier(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 33 for c in value):
        raise ValueError("native_context_preparation_identifier_invalid")
    return value


def snapshot_chunks(raw: Any) -> str:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
        raise ValueError("native_context_chunks_required")
    chunks = []
    for item in raw:
        content, source, kind, sensitivity = native_context_chunk_fields(item)
        if (
            not isinstance(content, str) or not content or len(content.encode("utf-8")) > MAX_NATIVE_CONTEXT_BYTES
            or not isinstance(source, str) or not source or len(source) > 1024
            or "://" in source or any(ord(c) < 32 for c in source)
        ):
            raise ValueError("native_context_chunk_invalid")
        chunk = {"source_ref": source, "source_type": kind.value, "content": content}
        if sensitivity is not None:
            chunk["sensitivity"] = sensitivity.value
        chunks.append(chunk)
    rendered = json.dumps(chunks, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if len(rendered.encode("utf-8")) > MAX_NATIVE_CONTEXT_BYTES:
        raise ValueError("native_context_content_invalid")
    return rendered


@dataclass(frozen=True, slots=True)
class NativeContextSnapshot:
    bundle_id: str
    task_id: str
    chunks_json: str
    metadata_json: str

    @classmethod
    def create(
        cls, *, task_id: str, command: Mapping[str, Any], source_bundle_id: str,
        source_task_id: str, chunks: Any, access: Mapping[str, str],
    ) -> NativeContextSnapshot:
        task_id = bounded_context_identifier(task_id)
        command_digest = native_context_digest(dict(command))
        chunks_json = snapshot_chunks(chunks)
        metadata = {
            "native_context_access": dict(access),
            "native_context_snapshot": {
                "schema": "ananta.native-context-snapshot.v1", "command_digest": command_digest,
                "source_bundle_id": bounded_context_identifier(source_bundle_id),
                "source_task_id": bounded_context_identifier(source_task_id),
                "chunks_digest": native_context_digest(json.loads(chunks_json)),
            },
        }
        # Content changes at the same command cannot create a replacement ID.
        identity = native_context_digest({"hub_task_id": task_id, "command_digest": command_digest})
        return cls("nctx-" + identity[:40], task_id, chunks_json, json.dumps(metadata, sort_keys=True))

    def values(self) -> dict[str, Any]:
        return {
            "id": self.bundle_id, "task_id": self.task_id, "bundle_type": "native_pi_context",
            "context_text": None, "retrieval_run_id": None,
            "chunks": json.loads(self.chunks_json), "bundle_metadata": json.loads(self.metadata_json),
        }

    def matches(self, stored: Any) -> bool:
        def field(name):
            return stored.get(name) if isinstance(stored, Mapping) else getattr(stored, name, None)

        return stored is not None and all(field(name) == value for name, value in self.values().items())


class NativeContextSnapshotPort(Protocol):
    def ensure(self, snapshot: NativeContextSnapshot) -> None: ...


def validate_prepared_native_context(*, task: Mapping[str, Any], bundle: Any, command: Mapping[str, Any]) -> None:
    """Re-read the prepared snapshot's immutable bindings before policy output."""
    metadata = command["node"]["metadata"]
    if {"context_bundle_mode", "context_policy_id", "context_destination_id"}.isdisjoint(metadata):
        return  # Existing explicitly task-owned bundles keep their compatibility path.

    def field(name):
        return bundle.get(name) if isinstance(bundle, Mapping) else getattr(bundle, name, None)

    stored = field("bundle_metadata")
    lineage = stored.get("native_context_snapshot") if isinstance(stored, Mapping) else None
    if (
        metadata.get("context_bundle_mode") != "control_task" or not isinstance(lineage, Mapping)
        or task.get("parent_task_id") != command["control_task_id"]
    ):
        raise ValueError("native_context_snapshot_binding_mismatch")
    expected = NativeContextSnapshot.create(
        task_id=task["id"], command=command, source_bundle_id=lineage.get("source_bundle_id"),
        source_task_id=command["control_task_id"], chunks=field("chunks"), access={
            "policy_id": bounded_context_identifier(metadata.get("context_policy_id")),
            "destination_id": bounded_context_identifier(metadata.get("context_destination_id")),
            "provider_endpoint_identity": command["provider_binding"]["endpoint_identity"],
        },
    )
    if not expected.matches(bundle) or task.get("context_bundle_id") != expected.bundle_id:
        raise ValueError("native_context_snapshot_binding_mismatch")
