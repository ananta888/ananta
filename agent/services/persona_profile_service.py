"""Authorized profile metadata; saving never grants media publication rights."""

from typing import Protocol

from agent.models.persona_media import PersonaMediaProfile
from agent.services.organization_membership_service import OrganizationAccessPrincipal
from agent.services.persona_media_resolution import resolve_profile_layers
from agent.services.project_access_authority import ProjectAccessError, ProjectCapability


class PersonaProfileOwners(Protocol):
    def require(self, tenant, project, organization, kind, owner, *, mutable): ...
    def lineage(self, tenant, project, organization, kind, owner): ...


class PersonaProfileImagePort(Protocol):
    def require_reference(self, principal, reference): ...


class PersonaProfileService:
    def __init__(self, *, access, memberships, owners: PersonaProfileOwners, profiles, images: PersonaProfileImagePort):
        self.access, self.memberships, self.owners = access, memberships, owners
        self.profiles, self.images = profiles, images

    def _authorize(self, principal, project, organization, kind, owner, *, mutable):
        if (
            set(principal.roles) & {"worker", "service"}
            or not principal.subject_id
            or not principal.tenant_id
            or (principal.project_id and principal.project_id != project)
        ):
            raise PermissionError("persona_profile_user_scope_required")
        self.access.require(
            tenant_id=principal.tenant_id,
            project_id=project,
            subject_id=principal.subject_id,
            capability=ProjectCapability.MANAGE if mutable else ProjectCapability.READ,
            tenant_admin=principal.is_admin,
        )
        scope = dict(
            principal=OrganizationAccessPrincipal(
                principal_id=principal.subject_id,
                tenant_id=principal.tenant_id,
                project_id=principal.project_id,
            ),
            tenant_id=principal.tenant_id,
            project_id=project,
            organization_id=organization,
        )
        allowed = (
            self.memberships.can_mutate(**scope, grant_kind="persona_media")
            if mutable
            else self.memberships.can_view(**scope)
        )
        if not allowed:
            raise PermissionError("persona_profile_organization_denied")
        self.owners.require(principal.tenant_id, project, organization, kind, owner, mutable=mutable)

    def _images(self, principal, profile):
        for kind in ("voice", "video", "style"):
            if getattr(profile, kind).asset is not None:
                raise ValueError("persona_profile_media_not_supported")
        reference = profile.image.asset
        if reference is None:
            return
        self.images.require_reference(principal, reference)

    def current(self, principal, project, organization, kind, owner):
        self._authorize(principal, project, organization, kind, owner, mutable=False)
        profile = self.profiles.current(
            tenant_id=principal.tenant_id,
            project_id=project,
            owner_kind=kind,
            owner_id=owner,
        )
        available = True
        if profile is not None:
            try:
                self._images(principal, profile)
            except (ValueError, PermissionError, ProjectAccessError):
                available = False
        self._authorize(principal, project, organization, kind, owner, mutable=False)
        # A revoked asset must not trap an authorized owner in an uneditable
        # profile. Expose the CAS revision, not unavailable media references.
        return dict(
            profile=profile.model_dump(mode="json") if profile is not None and available else None,
            revision=profile.revision if profile else 0,
            content_hash=profile.content_hash() if profile else None,
            media_available=available,
            tenant_id=principal.tenant_id,
        )

    def save(self, principal, project, organization, kind, owner, profile: PersonaMediaProfile, *, expected_revision):
        if (profile.tenant_id, profile.project_id, profile.owner_kind, profile.owner_id) != (
            principal.tenant_id,
            project,
            kind,
            owner,
        ):
            raise PermissionError("persona_profile_scope_mismatch")
        self._authorize(principal, project, organization, kind, owner, mutable=True)
        self._images(principal, profile)
        self._authorize(principal, project, organization, kind, owner, mutable=True)
        return self.profiles.append(profile, expected_revision=expected_revision, actor=principal.subject_id)

    def effective(self, principal, project, organization, kind, owner):
        self._authorize(principal, project, organization, kind, owner, mutable=False)
        lineage = self.owners.lineage(principal.tenant_id, project, organization, kind, owner)
        layers, stamp = lineage
        profiles = []
        for layer_kind, layer_owner in layers:
            self._authorize(principal, project, organization, layer_kind, layer_owner, mutable=False)
            profiles.append(
                self.profiles.current(
                    tenant_id=principal.tenant_id,
                    project_id=project,
                    owner_kind=layer_kind,
                    owner_id=layer_owner,
                )
            )
        media = resolve_profile_layers(principal.tenant_id, project, dict(layers), tuple(p for p in profiles if p))
        for (layer_kind, layer_owner), previous in zip(layers, profiles, strict=True):
            self._authorize(principal, project, organization, layer_kind, layer_owner, mutable=False)
            current = self.profiles.current(
                tenant_id=principal.tenant_id,
                project_id=project,
                owner_kind=layer_kind,
                owner_id=layer_owner,
            )
            if current != previous:
                raise ValueError("persona_profile_revision_changed")
        if self.owners.lineage(principal.tenant_id, project, organization, kind, owner) != lineage:
            raise PermissionError("persona_lineage_changed")
        return {
            "media": [self._preview_selection(principal, selection) for selection in media],
            "topology_revision": stamp[0],
            "purpose": "preview",
            "runtime_bound": False,
        }

    def _preview_selection(self, principal, selection):
        available = selection.state != "missing"
        if selection.asset is not None:
            try:
                if selection.kind != "image":
                    raise ValueError("persona_profile_media_not_supported")
                self.images.require_reference(principal, selection.asset)
            except (ValueError, PermissionError, ProjectAccessError):
                available = False
        item = selection.model_dump(mode="json")
        if not available:
            item["asset"] = None
        return item | {
            "preview_allowed": available and selection.state == "asset",
            "publication_checked": False,
            "available": available,
        }
