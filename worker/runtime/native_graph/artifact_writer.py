"""Task-bound immutable local artifact registration for delegated Native outputs."""

import hashlib

from sqlalchemy.orm import Session

from ananta_contracts.native_context_bundle import native_context_digest


class LocalNativeArtifactWriter:
    def __init__(self, *, engine, artifact_model, version_model, storage):
        self._engine, self._artifact_model, self._version_model, self._storage = (
            engine, artifact_model, version_model, storage,
        )

    def store(self, *, filename, media_type, content, binding):
        sha256 = hashlib.sha256(content).hexdigest()
        identity = native_context_digest({"binding": binding, "filename": filename,
                                          "media_type": media_type, "sha256": sha256, "size_bytes": len(content)})
        artifact_id, version_id = f"native-local-{identity}", f"native-local-version-{identity}"
        stored = self._storage.store_immutable_bytes(
            artifact_id=artifact_id, version_number=1, filename=filename, content=content,
            expected_sha256=sha256, media_type=media_type,
        )
        if (
            stored.get("sha256") != sha256 or type(stored.get("size_bytes")) is not int
            or stored["size_bytes"] != len(content)
        ):
            raise ValueError("native_output_local_storage_invalid")
        metadata = {"native_output_binding": dict(binding)}
        with Session(self._engine) as session:
            artifact = session.get(self._artifact_model, artifact_id)
            version = session.get(self._version_model, version_id)
            if artifact is not None or version is not None:
                if (
                    artifact is None or version is None or artifact.artifact_metadata != metadata
                    or artifact.latest_version_id != version_id or artifact.latest_sha256 != sha256
                    or artifact.status != "stored" or artifact.size_bytes != len(content)
                    or artifact.latest_media_type != media_type or artifact.latest_filename != filename
                    or version.artifact_id != artifact_id or version.sha256 != sha256
                    or version.storage_path != stored["storage_path"] or version.size_bytes != len(content)
                    or version.version_metadata != metadata or version.version_number != 1
                    or version.original_filename != filename or version.media_type != media_type
                ):
                    raise ValueError("native_output_local_registration_conflict")
                return f"artifact://{artifact_id}"
            session.add(self._artifact_model(
                id=artifact_id, latest_version_id=version_id, latest_sha256=sha256, latest_media_type=media_type,
                latest_filename=filename, size_bytes=len(content), created_by="native_worker_output",
                artifact_metadata=metadata,
            ))
            session.add(self._version_model(
                id=version_id, artifact_id=artifact_id, storage_path=stored["storage_path"],
                original_filename=filename, media_type=media_type, size_bytes=len(content), sha256=sha256,
                version_metadata=metadata,
            ))
            session.commit()
        return f"artifact://{artifact_id}"
