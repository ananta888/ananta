"""Resolve Native result references against Hub-owned immutable registrations."""

import re

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.repositories.sqlalchemy_support import require_locked_row
from agent.services.native_artifact_dispatch import native_dispatch_binding_digest
from ananta_contracts.native_context_bundle import native_context_digest


def require_registered_native_artifacts(*, session, task, command, result, authority):
    """Check exact command/assignment/output bindings under the Task transaction.

    This verifies registered transfer admission and the exact current Native
    dispatch binding, not production evidence. The caller must first validate
    current lease/assignment/grant authority in this same transaction.
    """
    admitted = []
    expected_binding = {
        "hub_task_id": task.id, "command_digest": native_context_digest(command.to_dict()),
        "assignment_id": authority.assignment_id, "assignment_revision": authority.assignment_revision,
        "attempt_id": command.attempt_id, "fencing_token": command.fencing_token,
    }
    for name, reference in sorted(result.artifact_refs.items()):
        if (
            not isinstance(reference, str)
            or re.fullmatch(r"artifact://native-artifact-[0-9a-f]{64}", reference) is None
        ):
            raise ValueError("native_artifact_hub_receipt_required")
        artifact_id = reference.removeprefix("artifact://")
        artifact = require_locked_row(
            session, ArtifactDB, artifact_id, missing_reason="native_artifact_hub_receipt_required",
        )
        provenance = (artifact.artifact_metadata or {}).get("native_artifact_ingress")
        if not isinstance(provenance, dict):
            raise ValueError("native_artifact_hub_receipt_required")
        binding = provenance.get("binding")
        if (
            set(provenance) != {
                "schema", "classification", "binding", "tenant_id", "project_id", "output_name",
                "filename", "media_type", "size_bytes", "sha256",
            }
            or not isinstance(binding, dict)
            or set(binding) != {*expected_binding, "dispatch_lease_digest"}
            or any(native_context_digest(binding.get(key)) != native_context_digest(value)
                   for key, value in expected_binding.items())
            or not isinstance(binding.get("dispatch_lease_digest"), str)
            or re.fullmatch(r"[0-9a-f]{64}", binding["dispatch_lease_digest"]) is None
            or binding["dispatch_lease_digest"] != native_dispatch_binding_digest(command=command, authority=authority)
            or provenance.get("schema") != "ananta.native-artifact-binding.v1"
            or provenance.get("classification") != "technical_observation"
            or provenance.get("tenant_id") != task.tenant_id or provenance.get("project_id") != task.project_id
            or provenance.get("output_name") != name
        ):
            raise ValueError("native_artifact_result_binding_mismatch")
        digest = native_context_digest(provenance)
        version_id = f"native-version-{digest}"
        if artifact_id != f"native-artifact-{digest}" or artifact.latest_version_id != version_id:
            raise ValueError("native_artifact_registration_conflict")
        version = require_locked_row(
            session, ArtifactVersionDB, version_id, missing_reason="native_artifact_hub_receipt_required",
        )
        if (
            artifact.status != "stored" or artifact.created_by != "hub_native_artifact_ingress"
            or artifact.artifact_metadata != {"native_artifact_ingress": provenance}
            or version.version_metadata != {"native_artifact_ingress": provenance}
            or version.artifact_id != artifact_id or version.version_number != 1
            or any(getattr(artifact, field) != provenance[key] for field, key in (
                ("latest_sha256", "sha256"), ("size_bytes", "size_bytes"),
                ("latest_media_type", "media_type"), ("latest_filename", "filename"),
            ))
            or any(getattr(version, field) != provenance[key] for field, key in (
                ("sha256", "sha256"), ("size_bytes", "size_bytes"),
                ("media_type", "media_type"), ("original_filename", "filename"),
            ))
            or type(provenance["size_bytes"]) is not int or provenance["size_bytes"] < 0
        ):
            raise ValueError("native_artifact_registration_conflict")
        admitted.append({
            "output_name": name, "artifact_id": artifact_id, "artifact_version_id": version_id,
            "binding_digest": digest,
        })
    return admitted
