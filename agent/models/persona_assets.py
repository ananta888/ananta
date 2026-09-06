"""Immutable asset metadata; named provenance bindings are not grants by themselves."""

from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from agent.models.persona_media import ClosedModel, Digest, Identifier, MediaAssetRef, Revision


class PersonaAssetAdmission(ClosedModel):
    """Trusted Hub policy snapshot, never reconstructed from upload metadata."""

    tenant_id: Identifier
    project_id: Identifier
    source_sha256: Digest
    origin_kind: Literal["upload", "generated", "licensed_pack"]
    origin_binding: Identifier
    license_binding: Identifier
    consent_binding: Identifier | None = None
    policy_binding: Identifier
    policy_revision: Revision
    classification: Literal["production", "synthetic", "test_only"]


class PersonaImageAsset(ClosedModel):
    schema_version: Literal["ananta.persona-image-asset.v1"] = "ananta.persona-image-asset.v1"
    image: MediaAssetRef
    preview: MediaAssetRef
    source_sha256: Digest
    origin_kind: Literal["upload", "generated", "licensed_pack"]
    origin_binding: Identifier
    license_binding: Identifier
    consent_binding: Identifier | None = None
    policy_binding: Identifier
    policy_revision: Revision
    inspection_task_id: Identifier
    inspection_lease_id: Identifier
    inspection_run_id: Annotated[str, Field(pattern=r"^RUN_[A-Za-z0-9_.:-]{1,156}$")] | None = None
    inspection_assignment_id: Identifier | None = None
    inspection_run_binding_digest: Digest | None = None
    image_size: Annotated[StrictInt, Field(gt=0, le=5 * 1024 * 1024)]
    preview_size: Annotated[StrictInt, Field(gt=0, le=350_000)]

    @model_validator(mode="after")
    def validate_bundle(self):
        identities = (self.inspection_run_id, self.inspection_assignment_id, self.inspection_run_binding_digest)
        if any(value is not None for value in identities) and not all(value is not None for value in identities):
            raise ValueError("persona_asset_inspection_binding_incomplete")
        if (
            self.image.kind != "image"
            or self.preview.kind != "image"
            or self.image.revision != 1
            or self.preview.revision != 1
            or self.image.artifact_id == self.preview.artifact_id
            or self.image.tenant_id != self.preview.tenant_id
            or self.image.project_id != self.preview.project_id
            or self.image.classification != self.preview.classification
        ):
            raise ValueError("persona_asset_bundle_invalid")
        return self
