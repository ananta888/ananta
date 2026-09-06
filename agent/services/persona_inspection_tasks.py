"""Normal Hub tasks and pre-reserved Registry runs for delegated image inspection."""

import hashlib
import json
import re
import time
import uuid
from typing import Protocol

from agent.services.persona_asset_service import PersonaInspectionResult


def admission_digest(admission):
    return hashlib.sha256(admission.model_dump_json().encode()).hexdigest()


def source_ids(admission):
    return tuple(
        sorted(
            value for value in (admission.origin_binding, admission.license_binding, admission.consent_binding) if value
        )
    )


def image_receipt(image, expected_source):
    if (
        image.source_sha256 != expected_source
        or not isinstance(image.png, bytes)
        or not isinstance(image.preview, bytes)
        or not 0 < len(image.png) <= 5 * 1024 * 1024
        or not 0 < len(image.preview) <= 350_000
        or hashlib.sha256(image.png).hexdigest() != image.image_sha256
        or hashlib.sha256(image.preview).hexdigest() != image.preview_sha256
    ):
        raise ValueError("persona_inspection_result_invalid")
    return receipt_digest(
        source_sha256=expected_source,
        image_sha256=image.image_sha256,
        preview_sha256=image.preview_sha256,
        image_size=len(image.png),
        preview_size=len(image.preview),
    )


def receipt_digest(*, source_sha256, image_sha256, preview_sha256, image_size, preview_size):
    payload = {
        "schema": "ananta.persona-inspection-receipt.v1",
        "source_sha256": source_sha256,
        "image_sha256": image_sha256,
        "preview_sha256": preview_sha256,
        "image_size": image_size,
        "preview_size": preview_size,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def task_context(assignment):
    return {
        key: assignment[key]
        for key in (
            "lease_id",
            "assignment_id",
            "run_id",
            "run_binding_digest",
            "admission_digest",
            "owner_subject",
            "source_sha256",
            "deadline",
        )
    }


class PersonaImageWorkerPort(Protocol):
    def execute(self, assignment: dict, content: bytes, media_type: str):
        """Execute only the closed Hub assignment in the isolated worker."""
        ...


class HubPersonaInspectionTasks:
    def __init__(
        self,
        *,
        policy,
        worker: PersonaImageWorkerPort,
        state,
        registry,
        repository_revision,
        execution_profile_digest,
        environment_digest,
        clock=time.time,
    ):
        if not isinstance(repository_revision, str) or not re.fullmatch(
            r"(?:[a-f0-9]{40}|[a-f0-9]{64})", repository_revision
        ):
            raise ValueError("persona_inspection_repository_revision_required")
        if any(
            not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in (execution_profile_digest, environment_digest)
        ):
            raise ValueError("persona_inspection_execution_binding_required")
        self.policy, self.worker, self.state, self.registry = policy, worker, state, registry
        self.repository_revision, self.profile_digest, self.environment_digest = (
            repository_revision,
            execution_profile_digest,
            environment_digest,
        )
        self.clock = clock

    def execute(self, principal, admission, content, media_type):
        self.policy.require_current(principal, admission, "inspect")
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= 5 * 1024 * 1024
            or hashlib.sha256(content).hexdigest() != admission.source_sha256
        ):
            raise ValueError("persona_inspection_input_mismatch")
        if media_type not in ("image/png", "image/jpeg"):
            raise ValueError("persona_inspection_media_type_invalid")
        task_id, assignment_id, lease_id = (str(uuid.uuid4()) for _ in range(3))
        test_only = admission.classification == "test_only"
        run = self.registry.reserve_run(
            tenant_id=admission.tenant_id,
            project_id=admission.project_id,
            task_id=task_id,
            assignment_id=assignment_id,
            dispatch_lease_id=lease_id,
            repository_revision=self.repository_revision,
            input_digest=admission.source_sha256,
            execution_profile_digest=self.profile_digest,
            environment_digest=self.environment_digest,
            source_ids=source_ids(admission),
            evidence_scope="test" if test_only else "local",
            synthetic=test_only,
            idempotency_key=f"persona-image-{task_id}",
        )
        assignment = {
            "schema": "ananta.persona-image-task.v1",
            "task_id": task_id,
            "assignment_id": assignment_id,
            "lease_id": lease_id,
            "tenant_id": admission.tenant_id,
            "project_id": admission.project_id,
            "run_id": run.run_id,
            "run_binding_digest": run.binding_digest,
            "admission_digest": admission_digest(admission),
            "owner_subject": principal.subject_id,
            "deadline": int(self.clock()) + 20,
            "source_sha256": admission.source_sha256,
        }
        recorded = False
        try:
            assignment["evidence"] = self.registry.assignment_projection(
                tenant_id=admission.tenant_id,
                project_id=admission.project_id,
                run_id=run.run_id,
                task_id=task_id,
                assignment_id=assignment_id,
                dispatch_lease_id=lease_id,
            )
            self.state.start(assignment, principal.subject_id, admission=admission)
            self.policy.require_current(principal, admission, "inspect")
            image = self.worker.execute(assignment, content, media_type)
            digest = image_receipt(image, admission.source_sha256)
            self.policy.require_current(principal, admission, "inspect")
            if not self.state.finish(assignment, "completed", receipt_digest=digest):
                raise ValueError("persona_inspection_task_cancelled")
            self.registry.record_result(
                tenant_id=admission.tenant_id,
                project_id=admission.project_id,
                run_id=run.run_id,
                assignment_id=assignment_id,
                dispatch_lease_id=lease_id,
                terminal_state="succeeded",
                result_digest=digest,
            )
            recorded = True
            self.policy.require_current(principal, admission, "inspect")
            return PersonaInspectionResult(task_id, lease_id, image, run.run_id, assignment_id, run.binding_digest)
        except Exception:
            try:
                self.state.finish(assignment, "failed")
            finally:
                if not recorded:
                    try:
                        self.registry.record_result(
                            tenant_id=admission.tenant_id,
                            project_id=admission.project_id,
                            run_id=run.run_id,
                            assignment_id=assignment_id,
                            dispatch_lease_id=lease_id,
                            terminal_state="failed",
                            result_digest=hashlib.sha256(b"persona_inspection_failed").hexdigest(),
                        )
                    except Exception:
                        pass  # Uncertain/terminal Registry state never authorizes an asset return.
            raise


class HubPersonaInspectionReceipts:
    def __init__(self, *, state, registry):
        self.state, self.registry = state, registry

    @staticmethod
    def _require_classification(run, classification):
        if (run.evidence_scope, run.synthetic) != (
            ("test", True) if classification == "test_only" else ("local", False)
        ):
            raise ValueError("persona_inspection_evidence_scope_mismatch")

    def require_asset(self, principal, asset):
        # Immutable catalog metadata remains verifiable after normal Task archival.
        # Policy/current membership and stored-byte hashes are checked by their own ports.
        if principal.tenant_id != asset.image.tenant_id or not asset.inspection_run_id:
            raise ValueError("persona_inspection_asset_unverified")
        run = self.registry.require_run_result(
            tenant_id=asset.image.tenant_id,
            project_id=asset.image.project_id,
            run_id=asset.inspection_run_id,
            task_id=asset.inspection_task_id,
            assignment_id=asset.inspection_assignment_id,
            dispatch_lease_id=asset.inspection_lease_id,
            input_digest=asset.source_sha256,
            source_ids=source_ids(asset),
            result_digest=receipt_digest(
                source_sha256=asset.source_sha256,
                image_sha256=asset.image.sha256,
                preview_sha256=asset.preview.sha256,
                image_size=asset.image_size,
                preview_size=asset.preview_size,
            ),
            expected_binding_digest=asset.inspection_run_binding_digest,
        )
        self._require_classification(run, asset.image.classification)

    def require_completed(self, principal, admission, result):
        task = self.state.get(result.task_id)
        context = (task.worker_execution_context or {}).get("persona_image", {}) if task else {}
        digest = image_receipt(result.image, admission.source_sha256)
        if (
            task is None
            or task.status != "completed"
            or task.task_kind != "persona_image_inspection"
            or principal.tenant_id != admission.tenant_id
            or context.get("owner_subject") != principal.subject_id
            or (task.tenant_id, task.project_id) != (admission.tenant_id, admission.project_id)
            or context.get("lease_id") != result.lease_id
            or context.get("assignment_id") != result.assignment_id
            or context.get("run_id") != result.run_id
            or context.get("run_binding_digest") != result.run_binding_digest
            or context.get("admission_digest") != admission_digest(admission)
            or context.get("result_digest") != digest
        ):
            raise ValueError("persona_inspection_receipt_mismatch")
        run = self.registry.require_run_result(
            tenant_id=admission.tenant_id,
            project_id=admission.project_id,
            run_id=result.run_id,
            task_id=result.task_id,
            assignment_id=result.assignment_id,
            dispatch_lease_id=result.lease_id,
            input_digest=admission.source_sha256,
            source_ids=source_ids(admission),
            result_digest=digest,
            expected_binding_digest=result.run_binding_digest,
        )
        self._require_classification(run, admission.classification)
