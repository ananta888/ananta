"""Image-selection adapter; metadata never substitutes for current asset policy."""


class PersonaProfileImages:
    def __init__(self, assets):
        self.assets = assets

    def reference(self, principal, project, artifact_id):
        self.assets.policy.require_lookup(principal, project, artifact_id, "preview")
        asset, _ = self.assets.catalog.get_active(principal.tenant_id, project, artifact_id)
        self.assets.policy.require_asset(principal, asset, "preview")
        return asset.image

    def require_reference(self, principal, reference):
        if reference.tenant_id != principal.tenant_id:
            raise PermissionError("persona_profile_image_scope_mismatch")
        if self.reference(principal, reference.project_id, reference.artifact_id) != reference:
            raise PermissionError("persona_profile_image_changed")
