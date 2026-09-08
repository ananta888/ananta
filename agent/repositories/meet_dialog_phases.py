"""Narrow phase metadata CAS through the existing Hub Task mutation/audit port."""

from copy import deepcopy

from agent.services.meet_contract import MeetError


class TaskDialogPhases:
    def __init__(self, tasks, *, task_status_cas):
        self.tasks, self.task_status_cas = tasks, task_status_cas

    def read(self, task_id):
        try:
            return self.tasks.get_by_id(task_id)
        except Exception:
            raise MeetError("meet_dialog_phase_storage_unavailable", 503) from None

    def replace(self, snapshot, record):
        from agent.services.meet_dialog_lifecycle import organization_tuple

        context = deepcopy(snapshot.worker_execution_context)
        organization = organization_tuple(snapshot)
        if snapshot.status != "in_progress" or snapshot.task_kind != "meet_dialog_session":
            return False
        try:
            return self.task_status_cas(
                snapshot.id,
                "in_progress",
                expected_statuses={"in_progress"},
                authoritative_predicate=lambda row: (
                    row.task_kind == "meet_dialog_session"
                    and not getattr(row, "archived", False)
                    and row.tenant_id == snapshot.tenant_id
                    and row.project_id == snapshot.project_id
                    and row.parent_task_id == snapshot.parent_task_id
                    and organization_tuple(row) == organization
                    and row.assigned_agent_url == snapshot.assigned_agent_url
                    and row.worker_execution_context == context
                ),
                worker_execution_context=context | {"meet_phase": deepcopy(record)},
                event_type="meet_dialog_phase_observed",
                event_actor="hub",
                event_details={"phase": record["phase"], "revision": record["revision"]},
            )
        except Exception:
            raise MeetError("meet_dialog_phase_storage_unavailable", 503) from None
