"""Publish delegated outputs through Hub admission; never create Hub identities."""

import base64
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import quote

from ananta_contracts.native_artifact_transfer import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_COUNT,
    MAX_TOTAL_BYTES,
    SCHEMA,
    NativeArtifactBytes,
    validate_native_artifact_receipts,
    validate_native_artifact_transfer,
)
from ananta_contracts.native_context_bundle import native_context_digest


class NativeArtifactTransportPort(Protocol):
    def request(self, *, method: str, path: str, payload: Mapping | None = None) -> Mapping: ...


class NativeArtifactPublisher:
    def __init__(self, *, transport: NativeArtifactTransportPort):
        self._transport = transport

    def publish(self, *, command, hub_task_id, project_id, artifacts: tuple[NativeArtifactBytes, ...]):
        if (
            not 1 <= len(artifacts) <= MAX_ARTIFACT_COUNT
            or any(type(item.content) is not bytes or len(item.content) > MAX_ARTIFACT_BYTES for item in artifacts)
            or sum(len(item.content) for item in artifacts) > MAX_TOTAL_BYTES
        ):
            raise ValueError("native_artifact_publication_size_invalid")
        path = f"/internal/tasks/{quote(hub_task_id, safe='')}/native-artifacts"
        projection = self._transport.request(method="GET", path=path)
        if not isinstance(projection, Mapping) or set(projection) != {"binding", "declared_outputs"}:
            raise ValueError("native_artifact_binding_response_invalid")
        binding = projection["binding"]
        if (
            not isinstance(binding, Mapping) or projection["declared_outputs"] != list(command.node.output_artifacts)
            or binding.get("hub_task_id") != hub_task_id
            or binding.get("command_digest") != native_context_digest(command.to_dict())
            or binding.get("attempt_id") != command.attempt_id
            or type(binding.get("fencing_token")) is not int or binding["fencing_token"] != command.fencing_token
        ):
            raise ValueError("native_artifact_binding_response_mismatch")
        transfer = {"schema": SCHEMA, "binding": dict(binding), "artifacts": [{
            "output_name": item.output_name, "filename": item.filename, "media_type": item.media_type,
            "size_bytes": len(item.content), "sha256": item.sha256,
            "content_base64": base64.b64encode(item.content).decode("ascii"),
        } for item in artifacts]}
        validate_native_artifact_transfer(
            transfer, expected_binding=binding, declared_outputs=command.node.output_artifacts,
        )
        receipts = self._transport.request(method="POST", path=path, payload=transfer)
        return validate_native_artifact_receipts(
            receipts, transfer=transfer, tenant_id=command.tenant_id, project_id=project_id,
        )


class NativeNodeArtifactPublication:
    def __init__(self, *, reader, publisher: NativeArtifactPublisher):
        self._reader, self._publisher = reader, publisher

    def publish(self, *, command, task, references):
        if set(references) != set(command.node.output_artifacts):
            raise ValueError("native_artifact_outputs_missing")
        artifacts = tuple(self._reader.read(
            command=command, task=task, output_name=name, reference=references[name],
        ) for name in command.node.output_artifacts)
        return self._publisher.publish(
            command=command, hub_task_id=task["id"], project_id=task.get("project_id"), artifacts=artifacts,
        )
