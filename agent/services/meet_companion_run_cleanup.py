"""Hub-owned cleanup after authoritative Task completion, with Registry checks."""

import time
from typing import Protocol

from agent.services.hub_evidence_registry_service import HubEvidenceRegistryError
from agent.services.meet_companion_task_tools import digest


class CompanionRunCleanupPort(Protocol):
    def reserved(self, **binding): ...

    def terminate(self, identity, **result): ...

    def unchanged(self, identity): ...


class MeetCompanionRunCleanup:
    def __init__(self, repository: CompanionRunCleanupPort, registry, *, clock=time.time):
        self.repository, self.registry, self.clock = repository, registry, clock

    def terminal(self, task, *, lease_id, runtime_id):
        context = (task.worker_execution_context or {}).get("meet_dialog", {})
        if (task.task_kind != "meet_dialog_session" or task.status not in {"completed", "failed", "cancelled"}
                or context.get("lease_id") != lease_id or context.get("runtime_id") != runtime_id):
            return 0
        binding = dict(tenant_id=task.tenant_id, project_id=task.project_id, task_id=task.id,
                       assignment_id=runtime_id, dispatch_lease_id=lease_id)
        count = 0
        for run in self.repository.reserved(**binding):
            try:
                self.registry.assignment_projection(**binding, run_id=run.run_id,
                                                    expected_binding_digest=run.binding_digest)
            except HubEvidenceRegistryError:
                if not self.repository.unchanged(run):
                    continue
                raise
            sources = [self.registry.require_registered_source(tenant_id=task.tenant_id,
                project_id=task.project_id, source_id=source_id) for source_id in run.source_ids]
            if not any(source.origin_type == "meet_companion_model" and source.content_digest == run.input_digest
                       for source in sources):
                continue
            count += self.repository.terminate(run,
                terminal_state="cancelled" if task.status == "cancelled" else "failed",
                result_digest=digest({"schema": "ananta.meet-companion-run-cleanup.v1",
                                      "reason": "task_terminal_without_model_result", "task_status": task.status}),
                updated_at_epoch=self.clock())
        return count
