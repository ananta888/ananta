"""Explicit project-managed image permissions pinned to registered source facts."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from agent.models.persona_media import ClosedModel, Digest, Identifier, Revision


class PersonaSourcePin(ClosedModel):
    source_id: Annotated[str, Field(pattern=r"^SRC_[A-Za-z0-9_.:-]{1,156}$")]
    binding_digest: Digest


class PersonaImagePolicy(ClosedModel):
    tenant_id: Identifier
    project_id: Identifier
    policy_binding: Identifier
    revision: Revision
    source: PersonaSourcePin
    license: PersonaSourcePin
    consent: PersonaSourcePin | None = None
    origin_kind: Literal["upload", "generated", "licensed_pack"]
    personal_likeness: StrictBool = True
    classification: Literal["production", "synthetic", "test_only"]
    subjects: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    purposes: tuple[Literal["inspect", "store", "preview", "publish"], ...] = Field(min_length=1, max_length=4)
    expires_at_ms: Annotated[StrictInt, Field(gt=0, lt=2**53)]

    @model_validator(mode="after")
    def validate_permissions(self):
        if self.policy_binding.startswith(("SRC_", "RUN_")):
            raise ValueError("persona_policy_identifier_is_not_evidence")
        if len(set(self.subjects)) != len(self.subjects) or len(set(self.purposes)) != len(self.purposes):
            raise ValueError("persona_policy_duplicates")
        if (self.personal_likeness or self.origin_kind == "upload") and self.consent is None:
            raise ValueError("persona_policy_consent_required")
        if self.classification == "production" and self.origin_kind == "generated":
            raise ValueError("persona_generated_image_must_be_labelled_synthetic")
        pins = (self.source.source_id, self.license.source_id) + ((self.consent.source_id,) if self.consent else ())
        if len(set(pins)) != len(pins):
            raise ValueError("persona_policy_proofs_must_be_separate")
        return self
