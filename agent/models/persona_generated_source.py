"""Closed output receipts; generated content is not synthetic test evidence."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_media import ClosedModel, Digest, Identifier


def receipt_digest(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class PersonaGeneratedOutput(ClosedModel):
    schema_version: Literal["ananta.persona-generated-output.v1"]
    tenant_id: Identifier
    project_id: Identifier
    owner_subject: Identifier
    media_kind: Literal["image", "video"]
    media_type: Literal["image/png", "image/jpeg", "video/mp4"]
    content_sha256: Digest
    content_size: Annotated[StrictInt, Field(gt=0, le=5 * 1024 * 1024)]
    inputs: tuple[PersonaSourcePin, ...] = Field(min_length=1, max_length=30)
    license: PersonaSourcePin
    consent: PersonaSourcePin | None
    personal_likeness: StrictBool
    classification: Literal["synthetic", "test_only"]

    @model_validator(mode="after")
    def require_consistency(self):
        if (
            (self.media_kind == "image") != self.media_type.startswith("image/")
            or (self.media_kind == "video" and self.content_size > 3_500_000)
            or (self.personal_likeness and self.consent is None)
        ):
            raise ValueError("persona_generation_media_or_consent_invalid")
        ids = [pin.source_id for pin in self.source_pins()]
        if len(ids) != len(set(ids)):
            raise ValueError("persona_generation_proofs_must_be_separate")
        return self

    def source_pins(self):
        return self.inputs + (self.license,) + ((self.consent,) if self.consent is not None else ())

    def digest(self):
        return receipt_digest(self.model_dump(mode="json"))


class PersonaGenerationRunPin(ClosedModel):
    run_id: Annotated[str, Field(pattern=r"^RUN_[A-Za-z0-9_.:-]{1,156}$")]
    task_id: Identifier
    assignment_id: Identifier
    dispatch_lease_id: Identifier
    input_digest: Digest
    expected_binding_digest: Digest
