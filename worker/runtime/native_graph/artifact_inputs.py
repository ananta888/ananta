"""Validate downloaded Hub bytes against the delegated Native input reference."""

from collections.abc import Mapping
from urllib.parse import quote

from ananta_contracts.native_artifact_transfer import (
    SCHEMA,
    validate_native_artifact_receipts,
    validate_native_artifact_transfer,
)
from ananta_contracts.native_context_bundle import native_context_digest


class NativeArtifactInputDownloader:
    def __init__(self, *, transport):
        self._transport = transport

    def download(self, *, command, hub_task_id, project_id, input_name):
        if input_name not in command.node.input_artifacts or input_name not in command.artifact_refs:
            raise ValueError("native_artifact_download_input_not_declared")
        response = self._transport.request(
            method="GET", path=f"/internal/tasks/{quote(hub_task_id, safe='')}/native-artifact-inputs/"
            f"{quote(input_name, safe='')}",
        )
        if (
            not isinstance(response, Mapping) or set(response) != {
                "schema", "hub_task_id", "input_name", "artifact_ref", "sha256", "size_bytes",
                "filename", "media_type", "content_base64", "consumer_command_digest", "provenance",
                "artifact_version_id",
            }
            or response["schema"] != "ananta.native-artifact-input.v1"
            or response["hub_task_id"] != hub_task_id or response["input_name"] != input_name
            or response["consumer_command_digest"] != native_context_digest(command.to_dict())
            or response["artifact_ref"] != command.artifact_refs[input_name]
            or not isinstance(response["provenance"], Mapping)
        ):
            raise ValueError("native_artifact_download_binding_invalid")
        provenance = response["provenance"]
        transfer = {"schema": SCHEMA, "binding": provenance.get("binding"), "artifacts": [{
            "output_name": input_name, "filename": response["filename"], "media_type": response["media_type"],
            "sha256": response["sha256"], "size_bytes": response["size_bytes"],
            "content_base64": response["content_base64"],
        }]}
        validate_native_artifact_receipts({"schema": "ananta.native-artifact-receipts.v1", "artifacts": [{
            **provenance, "artifact_id": response["artifact_ref"].removeprefix("artifact://"),
            "artifact_version_id": response["artifact_version_id"], "artifact_ref": response["artifact_ref"],
        }]}, transfer=transfer, tenant_id=command.tenant_id, project_id=project_id)
        item, = validate_native_artifact_transfer(
            transfer, expected_binding=provenance.get("binding"), declared_outputs=(input_name,),
        )
        return item
