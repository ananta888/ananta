"""Closed asset construction adapters; no policy, task or persistence decisions."""

import uuid
from typing import Protocol

from agent.models.persona_assets import PersonaImageAsset
from agent.models.persona_media import MediaAssetRef
from agent.models.persona_video_assets import PersonaVideoAsset, PersonaVideoInspectionBinding
from ananta_contracts.persona_video import MAX_INPUT_BYTES


class PersonaAssetAdmissionFormat(Protocol):
    kind: str
    maximum: int
    media_types: tuple[str, ...]

    def payload(self, result): ...
    def primary(self, asset) -> MediaAssetRef: ...
    def build(self, admission, result): ...


def reference(admission, *, kind, digest):
    return MediaAssetRef(
        tenant_id=admission.tenant_id,
        project_id=admission.project_id,
        classification=admission.classification,
        revision=1,
        kind=kind,
        sha256=digest,
        artifact_id=str(uuid.uuid4()),
    )


class PersonaImageAdmissionFormat:
    kind = "image"
    maximum = 5 * 1024 * 1024
    media_types = ("image/png", "image/jpeg")

    def payload(self, result):
        return result.image

    def primary(self, asset):
        return asset.image

    def build(self, admission, result):
        image = result.image
        return PersonaImageAsset(
            image=reference(admission, kind="image", digest=image.image_sha256),
            preview=reference(admission, kind="image", digest=image.preview_sha256),
            source_sha256=admission.source_sha256,
            origin_kind=admission.origin_kind,
            origin_binding=admission.origin_binding,
            license_binding=admission.license_binding,
            consent_binding=admission.consent_binding,
            policy_binding=admission.policy_binding,
            policy_revision=admission.policy_revision,
            inspection_task_id=result.task_id,
            inspection_lease_id=result.lease_id,
            inspection_run_id=result.run_id,
            inspection_assignment_id=result.assignment_id,
            inspection_run_binding_digest=result.run_binding_digest,
            image_size=len(image.png),
            preview_size=len(image.preview),
        )


class PersonaVideoAdmissionFormat:
    kind = "video"
    maximum = MAX_INPUT_BYTES
    media_types = ("video/mp4",)

    def payload(self, result):
        return result.video

    def primary(self, asset):
        return asset.video

    def build(self, admission, result):
        video = result.video
        return PersonaVideoAsset(
            video=reference(admission, kind="video", digest=video.video_sha256),
            preview=reference(admission, kind="image", digest=video.preview_sha256),
            admission=admission,
            inspection=PersonaVideoInspectionBinding(
                task_id=result.task_id,
                lease_id=result.lease_id,
                run_id=result.run_id,
                assignment_id=result.assignment_id,
                run_binding_digest=result.run_binding_digest,
            ),
            frames=video.frames,
            video_size=len(video.video),
            preview_size=len(video.preview),
        )
