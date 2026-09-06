"""Image/video execution and receipt adapters for the same Hub task lifecycle."""

from dataclasses import dataclass

from agent.services.persona_asset_service import PersonaInspectionResult
from agent.services.persona_inspection_contracts import image_receipt, receipt_digest, source_ids
from agent.services.persona_video_inspection import PersonaVideoInspectionResult, video_receipt, video_receipt_digest


@dataclass(frozen=True)
class InspectionAssetReceipt:
    tenant_id: str
    project_id: str
    run_id: str | None
    task_id: str
    assignment_id: str | None
    dispatch_lease_id: str
    input_digest: str
    source_ids: tuple[str, ...]
    result_digest: str
    expected_binding_digest: str | None


class PersonaImageInspectionFormat:
    kind = "image"
    maximum = 5 * 1024 * 1024
    media_types = ("image/png", "image/jpeg")
    receipt = staticmethod(image_receipt)

    def result(self, task, lease, payload, run, assignment):
        return PersonaInspectionResult(task, lease, payload, run.run_id, assignment, run.binding_digest)

    def payload(self, result):
        return result.image

    def classification(self, asset):
        return asset.image.classification

    def asset_receipt(self, asset):
        return InspectionAssetReceipt(
            asset.image.tenant_id,
            asset.image.project_id,
            asset.inspection_run_id,
            asset.inspection_task_id,
            asset.inspection_assignment_id,
            asset.inspection_lease_id,
            asset.source_sha256,
            source_ids(asset),
            receipt_digest(
                source_sha256=asset.source_sha256,
                image_sha256=asset.image.sha256,
                preview_sha256=asset.preview.sha256,
                image_size=asset.image_size,
                preview_size=asset.preview_size,
            ),
            asset.inspection_run_binding_digest,
        )


class PersonaVideoInspectionFormat:
    kind = "video"
    maximum = 3_500_000
    media_types = ("video/mp4",)
    receipt = staticmethod(video_receipt)

    def result(self, task, lease, payload, run, assignment):
        return PersonaVideoInspectionResult(task, lease, payload, run.run_id, assignment, run.binding_digest)

    def payload(self, result):
        return result.video

    def classification(self, asset):
        return asset.video.classification

    def asset_receipt(self, asset):
        value = asset.inspection
        return InspectionAssetReceipt(
            asset.video.tenant_id,
            asset.video.project_id,
            value.run_id,
            value.task_id,
            value.assignment_id,
            value.lease_id,
            asset.admission.source_sha256,
            source_ids(asset.admission),
            video_receipt_digest(
                source_sha256=asset.admission.source_sha256,
                video_sha256=asset.video.sha256,
                preview_sha256=asset.preview.sha256,
                frames=asset.frames,
                video_size=asset.video_size,
                preview_size=asset.preview_size,
            ),
            value.run_binding_digest,
        )
