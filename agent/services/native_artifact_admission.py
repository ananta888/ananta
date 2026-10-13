"""Hub-owned Native artifact materialization under the caller Task transaction.

Filesystem objects may survive a rolled-back transaction. Their content-bound
identity makes retry safe; only committed SQL rows constitute admitted outputs.
This service never commits the session or creates evidence-registry identities.
"""

import time

import sqlalchemy as sa

from agent.db_models import ArtifactDB, ArtifactVersionDB, TaskDB
from agent.ports.native_artifact_admission import NativeArtifactDispatchPort, NativeArtifactStoragePort
from agent.ports.native_result_authority import NativeResultAuthorityPort
from agent.services.native_result_task_projection import native_task_command
from ananta_contracts.native_artifact_transfer import validate_native_artifact_transfer
from ananta_contracts.native_context_bundle import native_context_digest


class NativeArtifactAdmissionService:
    def __init__(
        self, *, authority: NativeResultAuthorityPort, dispatch: NativeArtifactDispatchPort,
        storage: NativeArtifactStoragePort, clock=time.time,
    ):
        self._authority, self._dispatch, self._storage, self._clock = authority, dispatch, storage, clock

    def _current_binding(self, *, session, task_id):
        statement = sa.select(TaskDB).where(TaskDB.id == task_id).execution_options(populate_existing=True)
        if session.get_bind().dialect.name == "postgresql":
            statement = statement.with_for_update()
        with session.no_autoflush:
            task = session.execute(statement).scalar_one_or_none()
        if task is None:
            raise ValueError("native_artifact_task_missing")
        command = native_task_command(task)
        if task.status not in {"assigned", "queued", "running", "in_progress"}:
            raise ValueError("native_artifact_active_task_required")
        now = self._clock()
        authority = self._authority.require_current(session=session, command=command, task=task, now=now)
        lease_digest = self._dispatch.require_current_digest(session=session, task=task, command=command, now=now)
        binding = {
            "hub_task_id": task.id, "command_digest": native_context_digest(command.to_dict()),
            "assignment_id": authority.assignment_id, "assignment_revision": authority.assignment_revision,
            "attempt_id": command.attempt_id, "fencing_token": command.fencing_token,
            "dispatch_lease_digest": lease_digest,
        }
        return task, command, binding, now

    def transfer_binding(self, *, session, task_id):
        _, command, binding, _ = self._current_binding(session=session, task_id=task_id)
        return {"binding": binding, "declared_outputs": list(command.node.output_artifacts)}

    def materialize(self, *, session, task_id, transfer):
        task, command, binding, now = self._current_binding(session=session, task_id=task_id)
        # Validate all bytes before the first filesystem or SQL side effect.
        candidates = validate_native_artifact_transfer(
            transfer, expected_binding=binding, declared_outputs=command.node.output_artifacts,
        )
        receipts = []
        for item in candidates:
            provenance = {
                "schema": "ananta.native-artifact-binding.v1", "classification": "technical_observation",
                "binding": binding, "tenant_id": task.tenant_id, "project_id": task.project_id,
                "output_name": item.output_name, "filename": item.filename, "media_type": item.media_type,
                "size_bytes": len(item.content), "sha256": item.sha256,
            }
            digest = native_context_digest(provenance)
            artifact_id, version_id = f"native-artifact-{digest}", f"native-version-{digest}"
            artifact = session.get(ArtifactDB, artifact_id)
            version = session.get(ArtifactVersionDB, version_id)
            if (artifact is None) != (version is None):
                raise ValueError("native_artifact_registration_inconsistent")
            if artifact is not None and (
                artifact.artifact_metadata != {"native_artifact_ingress": provenance}
                or artifact.latest_version_id != version_id or artifact.status != "stored"
                or artifact.latest_sha256 != item.sha256 or artifact.size_bytes != len(item.content)
                or artifact.latest_filename != item.filename or artifact.latest_media_type != item.media_type
                or version.version_metadata != {"native_artifact_ingress": provenance}
                or version.artifact_id != artifact_id or version.version_number != 1
                or version.sha256 != item.sha256 or version.size_bytes != len(item.content)
                or version.original_filename != item.filename or version.media_type != item.media_type
            ):
                raise ValueError("native_artifact_registration_conflict")
            stored = self._storage.store_immutable_bytes(
                artifact_id=artifact_id, version_number=1, filename=item.filename, content=item.content,
                expected_sha256=item.sha256, media_type=item.media_type,
            )
            if (
                stored.get("sha256") != item.sha256 or type(stored.get("size_bytes")) is not int
                or stored["size_bytes"] != len(item.content) or not stored.get("storage_path")
                or version is not None and version.storage_path != str(stored["storage_path"])
            ):
                raise ValueError("native_artifact_storage_verification_failed")
            if artifact is None:
                session.add(ArtifactDB(
                    id=artifact_id, latest_version_id=version_id, latest_sha256=item.sha256,
                    latest_media_type=item.media_type, latest_filename=item.filename, size_bytes=len(item.content),
                    created_by="hub_native_artifact_ingress", artifact_metadata={"native_artifact_ingress": provenance},
                    created_at=now, updated_at=now,
                ))
                session.add(ArtifactVersionDB(
                    id=version_id, artifact_id=artifact_id, storage_path=str(stored["storage_path"]),
                    original_filename=item.filename, media_type=item.media_type, size_bytes=len(item.content),
                    sha256=item.sha256, version_metadata={"native_artifact_ingress": provenance}, created_at=now,
                ))
            receipts.append({
                **provenance, "artifact_id": artifact_id, "artifact_version_id": version_id,
                "artifact_ref": f"artifact://{artifact_id}",
            })
        session.flush()
        return tuple(receipts)
