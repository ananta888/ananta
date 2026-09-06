"""Immutable clip metadata; structural validity never substitutes for Hub policy."""

import re
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from agent.models.persona_assets import PersonaAssetAdmission
from agent.models.persona_media import ClosedModel, Digest, Identifier, MediaAssetRef


class PersonaVideoInspectionBinding(ClosedModel):
    task_id: Identifier
    lease_id: Identifier
    run_id: Annotated[str, Field(pattern=r"^RUN_[A-Za-z0-9_.:-]{1,156}$")]
    assignment_id: Identifier
    run_binding_digest: Digest


class PersonaVideoAsset(ClosedModel):
    schema_version: Literal["ananta.persona-video-asset.v1"] = "ananta.persona-video-asset.v1"
    profile: Literal["ananta.persona-clip.h264-256-12.v1"] = "ananta.persona-clip.h264-256-12.v1"
    video: MediaAssetRef
    preview: MediaAssetRef
    admission: PersonaAssetAdmission
    inspection: PersonaVideoInspectionBinding
    frames: Annotated[StrictInt, Field(ge=2, le=120)]
    video_size: Annotated[StrictInt, Field(ge=16, le=1_500_000)]
    preview_size: Annotated[StrictInt, Field(ge=33, le=350_000)]

    @model_validator(mode="after")
    def validate_bundle(self):
        expected = (self.admission.tenant_id, self.admission.project_id, self.admission.classification)
        for reference, kind in ((self.video, "video"), (self.preview, "image")):
            if (
                (reference.tenant_id, reference.project_id, reference.classification) != expected
                or reference.kind != kind
                or reference.revision != 1
            ):
                raise ValueError("persona_video_asset_scope_or_kind_mismatch")
        if self.video.artifact_id == self.preview.artifact_id:
            raise ValueError("persona_video_asset_duplicate_reference")
        if self.admission.origin_kind == "generated" and self.admission.classification == "production":
            raise ValueError("persona_generated_video_must_be_labelled_synthetic")
        bindings = [self.admission.origin_binding, self.admission.license_binding]
        if self.admission.consent_binding is not None:
            bindings.append(self.admission.consent_binding)
        if any(not re.fullmatch(r"SRC_[A-Za-z0-9_.:-]{1,156}", value) for value in bindings) or len(
            set(bindings)
        ) != len(bindings):
            raise ValueError("persona_video_source_bindings_invalid")
        return self
