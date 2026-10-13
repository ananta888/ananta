"""Hub reservation from a locked Pi Task and current dispatch authority."""

import time

from agent.db_models import TaskDB
from agent.repositories.sqlalchemy_support import require_locked_row
from agent.services.native_artifact_dispatch import native_dispatch_binding_digest
from agent.services.pi_context_evidence import PiContextEvidenceAdmission
from agent.services.pi_result_task_projection import pi_task_command
from ananta_contracts.native_context_bundle import native_context_digest


class PiRunEvidenceReservationService:
    def __init__(self, *, manifests, registry, authority, clock=time.time, contexts=None):
        self._manifests, self._registry, self._authority, self._clock = manifests, registry, authority, clock
        self._context_evidence = PiContextEvidenceAdmission(registry=registry, contexts=contexts)

    def assignment_projection(
        self, *, session, task_id, run_id, expected_binding_digest,
        manifest, source_id, source_binding_digest, policy_digest,
    ):
        task = require_locked_row(session, TaskDB, task_id, missing_reason="pi_evidence_task_missing")
        command = pi_task_command(task)
        if task.status not in {"created", "assigned", "queued"}:
            raise ValueError("pi_evidence_pre_execution_task_required")
        current = self._authority.require_current(session=session, task=task, command=command, now=self._clock())
        if (
            task.tenant_id != manifest.tenant_id or task.project_id != manifest.project_id
            or current.worker_id != manifest.worker_id or current.worker_url != manifest.worker_url
        ):
            raise ValueError("pi_evidence_manifest_task_binding_mismatch")
        admitted = self._manifests.require_admitted(
            manifest=manifest, source_id=source_id, source_binding_digest=source_binding_digest,
            policy_digest=policy_digest,
        )
        dispatch_id = "native-dispatch:" + native_dispatch_binding_digest(command=command, authority=current)
        projection = self._registry.assignment_projection(
            tenant_id=task.tenant_id, project_id=task.project_id, task_id=task.id, run_id=run_id,
            assignment_id=current.assignment_id, dispatch_lease_id=dispatch_id,
            input_digest=native_context_digest(task.worker_execution_context),
            expected_binding_digest=expected_binding_digest,
        )
        if admitted.source.source_id not in projection["source_ids"]:
            raise ValueError("pi_evidence_manifest_source_not_reserved")
        return projection

    def reserve(self, *, session, task_id, manifest, source_id, source_binding_digest, policy_digest):
        task = require_locked_row(session, TaskDB, task_id, missing_reason="pi_evidence_task_missing")
        command = pi_task_command(task)
        if task.status not in {"created", "assigned", "queued"}:
            raise ValueError("pi_evidence_pre_execution_task_required")
        current = self._authority.require_current(session=session, task=task, command=command, now=self._clock())
        if (
            task.tenant_id != manifest.tenant_id or task.project_id != manifest.project_id
            or current.worker_id != manifest.worker_id or current.worker_url != manifest.worker_url
        ):
            raise ValueError("pi_evidence_manifest_task_binding_mismatch")
        admitted = self._manifests.require_admitted(
            manifest=manifest, source_id=source_id, source_binding_digest=source_binding_digest,
            policy_digest=policy_digest,
        )
        input_digest = native_context_digest(task.worker_execution_context)
        context_source = self._context_evidence.admit(session=session, task=task, command=command,
            worker_id=current.worker_id, manifest=manifest, policy_digest=policy_digest)
        input_source = self._registry.register_source(
            tenant_id=task.tenant_id, project_id=task.project_id, origin_type="pi_native_task_input",
            origin_digest=native_context_digest({"task_id": task.id, "command_digest": native_context_digest(
                command.to_dict(),
            )}), content_digest=input_digest, policy_digest=policy_digest,
            evidence_scope=manifest.evidence_scope, synthetic=manifest.synthetic,
        )
        dispatch_id = "native-dispatch:" + native_dispatch_binding_digest(command=command, authority=current)
        return self._registry.reserve_run(
            tenant_id=task.tenant_id, project_id=task.project_id, task_id=task.id,
            assignment_id=current.assignment_id, dispatch_lease_id=dispatch_id,
            repository_revision=manifest.repository_revision, input_digest=input_digest,
            execution_profile_digest=native_context_digest({"profile": manifest.execution_profile}),
            environment_digest=manifest.environment_digest,
            source_ids=(admitted.source.source_id, input_source.source_id)
            + ((context_source.source_id,) if context_source is not None else ()),
            evidence_scope=manifest.evidence_scope, synthetic=manifest.synthetic,
            idempotency_key="pi-reservation:" + native_context_digest({
                "task_id": task.id, "assignment_id": current.assignment_id, "dispatch_id": dispatch_id,
            }),
        )
