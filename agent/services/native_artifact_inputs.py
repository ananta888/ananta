"""Hub-only input-byte access for one current Native Worker assignment."""

import base64
import re
import time
from dataclasses import fields

from agent.db_models import ArtifactDB, TaskDB
from agent.ports.native_result_authority import NativeResultAuthority
from agent.repositories.sqlalchemy_support import require_locked_row
from agent.services.native_artifact_result_admission import require_registered_native_artifacts
from agent.services.native_result_task_projection import native_task_command, require_native_result_receipt
from ananta_contracts.native_artifact_transfer import MAX_ARTIFACT_BYTES
from ananta_contracts.native_context_bundle import native_context_digest


class NativeArtifactInputService:
    def __init__(self, *, authority, dispatch, storage, clock=time.time):
        self._authority, self._dispatch, self._storage, self._clock = authority, dispatch, storage, clock

    def read(self, *, session, task_id, input_name):
        task = require_locked_row(session, TaskDB, task_id, missing_reason="native_artifact_input_task_missing")
        command = native_task_command(task)
        if task.status not in {"assigned", "queued", "running", "in_progress"}:
            raise ValueError("native_artifact_input_active_task_required")
        now = self._clock()
        self._authority.require_current(session=session, task=task, command=command, now=now)
        self._dispatch.require_current_digest(session=session, task=task, command=command, now=now)
        if input_name not in command.node.input_artifacts or input_name not in command.authorization.allowed_artifacts:
            raise ValueError("native_artifact_input_not_authorized")
        reference = command.artifact_refs.get(input_name)
        if (
            not isinstance(reference, str)
            or re.fullmatch(r"artifact://native-artifact-[0-9a-f]{64}", reference) is None
        ):
            raise ValueError("native_artifact_input_reference_invalid")
        artifact = require_locked_row(
            session, ArtifactDB, reference.removeprefix("artifact://"),
            missing_reason="native_artifact_input_not_registered",
        )
        provenance = (artifact.artifact_metadata or {}).get("native_artifact_ingress")
        binding = provenance.get("binding") if isinstance(provenance, dict) else None
        if (
            not isinstance(binding, dict) or provenance.get("tenant_id") != task.tenant_id
            or provenance.get("project_id") != task.project_id
            or type(provenance.get("size_bytes")) is not int or not 0 <= provenance["size_bytes"] <= MAX_ARTIFACT_BYTES
        ):
            raise ValueError("native_artifact_input_scope_mismatch")
        producer = require_locked_row(
            session, TaskDB, binding.get("hub_task_id"), missing_reason="native_artifact_input_producer_missing",
        )
        source = native_task_command(producer)
        if (
            producer.status != "completed" or producer.id == task.id or producer.project_id != task.project_id
            or any(getattr(source, key) != getattr(command, key) for key in (
                "tenant_id", "workflow_id", "run_id", "plan_hash", "policy_version", "control_task_id",
            ))
        ):
            raise ValueError("native_artifact_input_producer_scope_mismatch")
        result = require_native_result_receipt(producer, source)
        if result.artifact_refs.get(input_name) != reference:
            raise ValueError("native_artifact_input_result_binding_mismatch")
        receipt = producer.verification_status["native_result_receipt"]
        raw_authority = receipt.get("authority")
        if (
            not isinstance(raw_authority, dict)
            or set(raw_authority) != {field.name for field in fields(NativeResultAuthority)}
            or any(type(raw_authority.get(key)) is not int or raw_authority[key] < 1
                   for key in ("assignment_revision", "ownership_revision", "grant_revision"))
            or any(not isinstance(raw_authority.get(key), str) or not raw_authority[key]
                   for key in ("assignment_id", "worker_id", "worker_url"))
        ):
            raise ValueError("native_artifact_input_producer_receipt_invalid")
        require_registered_native_artifacts(
            session=session, task=producer, command=source, result=result,
            authority=NativeResultAuthority(**raw_authority),
        )
        content = self._storage.load_immutable_bytes(
            artifact_id=artifact.id, version_number=1, filename=provenance["filename"],
            expected_sha256=provenance["sha256"], expected_size=provenance["size_bytes"],
        )
        return {
            "schema": "ananta.native-artifact-input.v1", "hub_task_id": task.id, "input_name": input_name,
            "artifact_ref": reference, "sha256": provenance["sha256"], "size_bytes": len(content),
            "filename": provenance["filename"], "media_type": provenance["media_type"],
            "content_base64": base64.b64encode(content).decode("ascii"),
            "consumer_command_digest": native_context_digest(command.to_dict()),
            "provenance": provenance, "artifact_version_id": artifact.latest_version_id,
        }
