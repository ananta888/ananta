"""Normal Hub tasks and pre-reserved Registry runs for delegated media inspection."""

import hashlib
import re
import time
import uuid
from dataclasses import asdict
from typing import Protocol

from agent.services.persona_inspection_contracts import admission_digest, source_ids
from agent.services.persona_inspection_contracts import image_receipt as image_receipt
from agent.services.persona_inspection_contracts import receipt_digest as receipt_digest
from agent.services.persona_inspection_contracts import task_context as task_context
from agent.services.persona_inspection_formats import PersonaImageInspectionFormat


class PersonaInspectionWorkerPort(Protocol):
    def execute(self, assignment: dict, content: bytes, media_type: str):
        """Execute only the closed Hub assignment in the isolated worker."""
        ...


PersonaImageWorkerPort = PersonaInspectionWorkerPort  # Existing import compatibility.


class HubPersonaInspectionTasks:
    def __init__(
        self,
        *,
        policy,
        worker: PersonaInspectionWorkerPort,
        state,
        registry,
        repository_revision,
        execution_profile_digest,
        environment_digest,
        clock=time.time,
        format=None,
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
        self.format = format if format is not None else PersonaImageInspectionFormat()

    def execute(self, principal, admission, content, media_type):
        kind_check = getattr(self.policy, "require_media_kind", None)
        if kind_check is not None:
            kind_check(self.format.kind)
        elif self.format.kind != "image":
            raise PermissionError("persona_video_policy_kind_required")
        self.policy.require_current(principal, admission, "inspect")
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= self.format.maximum
            or hashlib.sha256(content).hexdigest() != admission.source_sha256
        ):
            raise ValueError("persona_inspection_input_mismatch")
        if media_type not in self.format.media_types:
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
            idempotency_key=f"persona-{self.format.kind}-{task_id}",
        )
        assignment = {
            "schema": f"ananta.persona-{self.format.kind}-task.v1",
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
            inspected = self.worker.execute(assignment, content, media_type)
            digest = self.format.receipt(inspected, admission.source_sha256)
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
            return self.format.result(task_id, lease_id, inspected, run, assignment_id)
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
    def __init__(self, *, state, registry, format=None):
        self.state, self.registry = state, registry
        self.format = format if format is not None else PersonaImageInspectionFormat()

    @staticmethod
    def _require_classification(run, classification):
        if (run.evidence_scope, run.synthetic) != (
            ("test", True) if classification == "test_only" else ("local", False)
        ):
            raise ValueError("persona_inspection_evidence_scope_mismatch")

    def require_asset(self, principal, asset):
        # Immutable catalog metadata remains verifiable after normal Task archival.
        # Policy/current membership and stored-byte hashes are checked by their own ports.
        pin = self.format.asset_receipt(asset)
        if principal.tenant_id != pin.tenant_id or not pin.run_id:
            raise ValueError("persona_inspection_asset_unverified")
        run = self.registry.require_run_result(**asdict(pin))
        self._require_classification(run, self.format.classification(asset))

    def require_completed(self, principal, admission, result):
        task = self.state.get(result.task_id)
        context = (task.worker_execution_context or {}).get(f"persona_{self.format.kind}", {}) if task else {}
        digest = self.format.receipt(self.format.payload(result), admission.source_sha256)
        if (
            task is None
            or task.status != "completed"
            or task.task_kind != f"persona_{self.format.kind}_inspection"
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
