"""Closed voice inspection receipt; task execution and identity issuance stay in the Hub."""

import hashlib
import json
from dataclasses import dataclass, field

from ananta_contracts.persona_voice import (
    MAX_DESCRIPTOR_BYTES,
    MEDIA_TYPE,
    InspectedVoiceDescriptor,
    inspect_voice_descriptor,
    voice_descriptor,
)


@dataclass(frozen=True)
class PersonaVoiceInspectionResult:
    task_id: str
    lease_id: str
    voice: InspectedVoiceDescriptor = field(repr=False)
    run_id: str
    assignment_id: str
    run_binding_digest: str


def voice_receipt_digest(*, source_sha256, voice_id, descriptor_size):
    payload = {
        "schema": "ananta.persona-voice-receipt.v1",
        "source_sha256": source_sha256,
        "voice_id": voice_id,
        "descriptor_size": descriptor_size,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def voice_receipt(voice, expected_source):
    if type(voice) is not InspectedVoiceDescriptor:
        raise ValueError("persona_voice_inspection_invalid")
    checked = inspect_voice_descriptor(voice.descriptor)
    if checked != voice or checked.source_sha256 != expected_source:
        raise ValueError("persona_voice_inspection_mismatch")
    return voice_receipt_digest(
        source_sha256=checked.source_sha256, voice_id=checked.voice_id, descriptor_size=len(checked.descriptor)
    )


class PersonaVoiceInspectionFormat:
    kind = "voice"
    maximum = MAX_DESCRIPTOR_BYTES
    media_types = (MEDIA_TYPE,)
    receipt = staticmethod(voice_receipt)

    def result(self, task, lease, payload, run, assignment):
        return PersonaVoiceInspectionResult(task, lease, payload, run.run_id, assignment, run.binding_digest)

    def payload(self, result):
        return result.voice

    def classification(self, asset):
        return asset.voice.classification

    def asset_receipt(self, asset):
        from agent.models.persona_voice_assets import PersonaVoiceAsset
        from agent.services.persona_inspection_contracts import source_ids
        from agent.services.persona_inspection_formats import InspectionAssetReceipt

        asset = PersonaVoiceAsset.model_validate_json(asset.model_dump_json())
        value = asset.inspection
        return InspectionAssetReceipt(
            asset.voice.tenant_id,
            asset.voice.project_id,
            value.run_id,
            value.task_id,
            value.assignment_id,
            value.lease_id,
            asset.admission.source_sha256,
            source_ids(asset.admission),
            voice_receipt(inspect_voice_descriptor(voice_descriptor(asset.voice_id)), asset.admission.source_sha256),
            value.run_binding_digest,
        )
