"""Video selection metadata requires current private asset/receipt permission."""


class PersonaProfileVideos:
    def __init__(self, assets):
        self.assets = assets

    def reference(self, principal, project, artifact_id):
        self.assets.policy.require_media_kind("video")
        self.assets.policy.require_lookup(principal, project, artifact_id, "preview")
        asset, revision = self.assets.catalog.get_active(principal.tenant_id, project, artifact_id)
        self.assets.policy.require_asset(principal, asset, "preview")
        if self.assets.catalog.get_active(principal.tenant_id, project, artifact_id) != (asset, revision):
            raise PermissionError("persona_profile_video_changed")
        return asset.video

    def require_reference(self, principal, reference):
        if reference.tenant_id != principal.tenant_id or reference.kind != "video":
            raise PermissionError("persona_profile_video_scope_mismatch")
        if self.reference(principal, reference.project_id, reference.artifact_id) != reference:
            raise PermissionError("persona_profile_video_changed")
