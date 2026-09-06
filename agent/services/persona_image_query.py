"""Bounded project image discovery filtered by current preview permissions."""

import time
from typing import Protocol

from agent.models.persona_media import MediaAssetRef
from agent.services.project_access_authority import ProjectAccessError


class ImageListPolicyPort(Protocol):
    def require_list(self, principal, project): ...


class PreviewImageReferencePort(Protocol):
    def reference(self, principal, project, artifact_id) -> MediaAssetRef: ...


class ImageScanPort(Protocol):
    def scan_active_ids(self, tenant, project, *, after, limit) -> tuple[str, ...]: ...


class ImageCursorPort(Protocol):
    def resolve(self, *, tenant_id, project_id, subject_id, token) -> str: ...
    def issue(self, *, tenant_id, project_id, subject_id, position) -> str: ...


class PersonaImageQuery:
    def __init__(
        self,
        *,
        policy: ImageListPolicyPort,
        catalog: ImageScanPort,
        images: PreviewImageReferencePort,
        cursors: ImageCursorPort,
        monotonic=time.monotonic,
    ):
        self.policy, self.catalog, self.images, self.cursors = policy, catalog, images, cursors
        self.monotonic = monotonic

    def query(self, principal, project, *, cursor, limit):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("persona_image_query_limit_invalid")
        self.policy.require_list(principal, project)
        scope = dict(tenant_id=principal.tenant_id, project_id=project, subject_id=principal.subject_id)
        after = self.cursors.resolve(**scope, token=cursor)
        candidates = self.catalog.scan_active_ids(principal.tenant_id, project, after=after, limit=65)
        items, position = [], after
        deadline = self.monotonic() + 3
        for artifact_id in candidates[:64]:
            if self.monotonic() >= deadline:
                break
            position = artifact_id
            try:
                reference = self.images.reference(principal, project, artifact_id)
                items.append(reference.model_dump(mode="json"))
            except (ValueError, PermissionError, ProjectAccessError):
                continue
            if len(items) == limit:
                break
        self.policy.require_list(principal, project)
        more = bool(candidates and position != candidates[-1])
        if more and position == after:
            raise ValueError("persona_image_query_deadline_exceeded")
        token = self.cursors.issue(**scope, position=position) if more else None
        return {"items": items, "next_cursor": token, "purpose": "preview"}
