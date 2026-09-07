"""Voice-only admission composition; descriptor validation is delegated, never a grant."""

from agent.models.persona_voice_assets import PersonaVoiceAsset, PersonaVoiceInspectionBinding
from agent.services.persona_asset_admission_formats import reference
from agent.services.persona_asset_lifecycle import PersonaAssetLifecycle
from ananta_contracts.persona_voice import MAX_DESCRIPTOR_BYTES, MEDIA_TYPE


class PersonaVoiceAdmissionFormat:
    kind = "voice"
    maximum = MAX_DESCRIPTOR_BYTES
    media_types = (MEDIA_TYPE,)

    def payload(self, result):
        return result.voice

    def primary(self, asset):
        return asset.voice

    def build(self, admission, result):
        return PersonaVoiceAsset(
            voice=reference(admission, kind="voice", digest=result.voice.source_sha256),
            voice_id=result.voice.voice_id,
            descriptor_size=len(result.voice.descriptor),
            admission=admission,
            inspection=PersonaVoiceInspectionBinding(
                task_id=result.task_id,
                lease_id=result.lease_id,
                run_id=result.run_id,
                assignment_id=result.assignment_id,
                run_binding_digest=result.run_binding_digest,
            ),
        )


def create_voice_asset_service(*, policy, tasks, catalog, storage):
    return PersonaAssetLifecycle(
        policy=policy, tasks=tasks, catalog=catalog, storage=storage, format=PersonaVoiceAdmissionFormat()
    )
