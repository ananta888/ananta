"""Explicit, resumable erasure of exact retired persona bundles; no broad sweep."""

from typing import Protocol


class PersonaPartErasurePort(Protocol):
    def erase(self, reference, expected_size, *, checkpoint) -> None: ...


PersonaImageErasurePort = PersonaPartErasurePort  # Existing image-only import compatibility.


class PersonaAssetErasureService:
    def __init__(self, *, policy, catalog, eraser: PersonaPartErasurePort, parts=None):
        self.policy, self.catalog, self.eraser = policy, catalog, eraser
        self.parts = parts if parts is not None else self._image_parts

    @staticmethod
    def _image_parts(asset):
        return ((asset.image, asset.image_size), (asset.preview, asset.preview_size))

    def status(self, principal, project, artifact_id):
        self.policy.require_revoke(principal, project, artifact_id)
        _, revision, state = self.catalog.get_retired(principal.tenant_id, project, artifact_id)
        return {"revision": revision, "state": state}

    def purge(self, principal, project, artifact_id, *, expected_revision, require_current=lambda: None):
        require_current()
        self.policy.require_revoke(principal, project, artifact_id)
        asset, revision, state = self.catalog.get_retired(principal.tenant_id, project, artifact_id)
        if type(expected_revision) is not int or expected_revision != revision:
            raise ValueError("persona_asset_purge_revision_conflict")
        if state == "purged":
            return revision
        if state != "purging":
            revision = self.catalog.transition(
                principal.tenant_id,
                project,
                artifact_id,
                expected_revision=revision,
                state="purging",
                actor=principal.subject_id,
            )

        def checkpoint():
            require_current()
            self.policy.require_revoke(principal, project, artifact_id)
            if self.catalog.get_retired(principal.tenant_id, project, artifact_id) != (asset, revision, "purging"):
                raise ValueError("persona_asset_purge_changed")

        # A durable purging tombstone precedes every destructive operation.
        # Interrupted erasure can retry these exact immutable names; missing
        # files are acceptable, changed bytes or symlinks are not.
        with self.catalog.storage_guard(
            principal.tenant_id, project, artifact_id, expected_revision=revision, state="purging"
        ):
            for reference, size in self.parts(asset):
                checkpoint()
                self.eraser.erase(reference, size, checkpoint=checkpoint)
        checkpoint()
        return self.catalog.transition(
            principal.tenant_id,
            project,
            artifact_id,
            expected_revision=revision,
            state="purged",
            actor=principal.subject_id,
        )
