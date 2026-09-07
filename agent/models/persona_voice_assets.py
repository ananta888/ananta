"""Immutable shipped voice descriptor; inspection identities still need Hub verification."""

import re
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from agent.models.persona_assets import PersonaAssetAdmission
from agent.models.persona_media import ClosedModel, Digest, Identifier, MediaAssetRef
from ananta_contracts.persona_voice import MAX_DESCRIPTOR_BYTES, inspect_voice_descriptor, voice_descriptor


class PersonaVoiceInspectionBinding(ClosedModel):
    task_id: Identifier
    lease_id: Identifier
    run_id: Annotated[str, Field(pattern=r"^RUN_[A-Za-z0-9_.:-]{1,156}$")]
    assignment_id: Identifier
    run_binding_digest: Digest


class PersonaVoiceAsset(ClosedModel):
    schema_version: Literal["ananta.persona-voice-asset.v1"] = "ananta.persona-voice-asset.v1"
    voice: MediaAssetRef
    voice_id: Annotated[str, Field(min_length=1, max_length=160)]
    descriptor_size: Annotated[StrictInt, Field(gt=0, le=MAX_DESCRIPTOR_BYTES)]
    admission: PersonaAssetAdmission
    inspection: PersonaVoiceInspectionBinding

    @model_validator(mode="after")
    def validate_descriptor(self):
        admission, reference = self.admission, self.voice
        if (
            (reference.tenant_id, reference.project_id, reference.classification)
            != (admission.tenant_id, admission.project_id, admission.classification)
            or reference.kind != "voice"
            or reference.revision != 1
        ):
            raise ValueError("persona_voice_asset_scope_or_kind_mismatch")
        if admission.origin_kind != "licensed_pack":
            raise ValueError("persona_voice_preset_origin_required")
        pins = [admission.origin_binding, admission.license_binding]
        if admission.consent_binding is not None:
            pins.append(admission.consent_binding)
        if len(set(pins)) != len(pins) or any(not re.fullmatch(r"SRC_[A-Za-z0-9_.:-]{1,156}", pin) for pin in pins):
            raise ValueError("persona_voice_source_bindings_invalid")
        inspected = inspect_voice_descriptor(voice_descriptor(self.voice_id))
        if (
            reference.sha256 != inspected.source_sha256
            or admission.source_sha256 != inspected.source_sha256
            or self.descriptor_size != len(inspected.descriptor)
        ):
            raise ValueError("persona_voice_descriptor_binding_mismatch")
        return self
