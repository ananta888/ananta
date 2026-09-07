"""Small explicit policy domains; image permissions never imply video rights."""

from typing import Protocol

from agent.models.persona_asset_policy import PersonaImagePolicy, PersonaVideoPolicy, PersonaVoicePolicy
from agent.models.persona_assets import PersonaAssetAdmission


class PersonaPolicyDomain(Protocol):
    @property
    def policy_type(self) -> type: ...
    @property
    def source_origin_type(self) -> str | None: ...
    def asset_admission(self, asset) -> PersonaAssetAdmission: ...


class PersonaImagePolicyDomain:
    policy_type = PersonaImagePolicy
    source_origin_type = None  # Preserve the previously admitted image source kinds.

    def asset_admission(self, asset):
        return PersonaAssetAdmission(
            tenant_id=asset.image.tenant_id,
            project_id=asset.image.project_id,
            source_sha256=asset.source_sha256,
            origin_kind=asset.origin_kind,
            origin_binding=asset.origin_binding,
            license_binding=asset.license_binding,
            consent_binding=asset.consent_binding,
            policy_binding=asset.policy_binding,
            policy_revision=asset.policy_revision,
            classification=asset.image.classification,
        )


class PersonaVideoPolicyDomain:
    policy_type = PersonaVideoPolicy
    source_origin_type = "persona_video"

    def asset_admission(self, asset):
        from agent.models.persona_video_assets import PersonaVideoAsset

        return PersonaVideoAsset.model_validate_json(asset.model_dump_json()).admission


class PersonaVoicePolicyDomain:
    policy_type = PersonaVoicePolicy
    source_origin_type = "persona_voice"

    def asset_admission(self, asset):
        from agent.models.persona_voice_assets import PersonaVoiceAsset

        return PersonaVoiceAsset.model_validate_json(asset.model_dump_json()).admission
