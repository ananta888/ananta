"""Explicit, resumable erasure of already-retired image bundles; no broad sweep."""

from typing import Protocol


class PersonaImageErasurePort(Protocol):
    def erase(self, reference, expected_size, *, checkpoint) -> None: ...


class PersonaAssetErasureService:
    def __init__(self, *, policy, catalog, eraser: PersonaImageErasurePort):
        self.policy, self.catalog, self.eraser = policy, catalog, eraser

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
            for reference, size in ((asset.image, asset.image_size), (asset.preview, asset.preview_size)):
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
