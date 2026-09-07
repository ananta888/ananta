"""Passive voice references require current policy/receipt, never synthesis permission."""


class PersonaProfileVoices:
    def __init__(self, assets):
        self.assets = assets

    def reference(self, principal, project, artifact_id):
        self.assets.policy.require_media_kind("voice")
        self.assets.policy.require_lookup(principal, project, artifact_id, "preview")
        asset, revision = self.assets.catalog.get_active(principal.tenant_id, project, artifact_id)
        self.assets.policy.require_asset(principal, asset, "preview")
        if self.assets.catalog.get_active(principal.tenant_id, project, artifact_id) != (asset, revision):
            raise PermissionError("persona_profile_voice_changed")
        return asset.voice

    def require_reference(self, principal, reference):
        if reference.tenant_id != principal.tenant_id or reference.kind != "voice":
            raise PermissionError("persona_profile_voice_scope_mismatch")
        if self.reference(principal, reference.project_id, reference.artifact_id) != reference:
            raise PermissionError("persona_profile_voice_changed")
