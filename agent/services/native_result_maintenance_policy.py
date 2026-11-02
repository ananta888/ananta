"""Closed Hub-owned preauthorization and deterministic finite maintenance plans."""

from agent.services.native_graph_models import NativeGraphRequest
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from ananta_contracts.native_result_maintenance import (
    MAINTENANCE_CAPABILITY,
    MAINTENANCE_TASK_KIND,
    MAINTENANCE_TOOL,
    MaintenanceSchedule,  # noqa: F401  (compat re-export, CLN-047)
    require_result_store_identifier,
)
from ananta_contracts.native_result_retirement import RETIREMENT_CAPABILITY, RETIREMENT_TOOL


def configured_maintenance_schedules(value):
    if value is None:
        return ()
    fields = {"enabled", "auto_approval", "policy_revision", "schedules"}
    if not isinstance(value, dict) or set(value) - fields or type(value.get("enabled", False)) is not bool:
        raise ValueError("native_maintenance_policy_invalid")
    if not value.get("enabled", False):
        return ()
    if value.get("auto_approval") is not True:
        raise ValueError("native_maintenance_preauthorization_required")
    revision = require_result_store_identifier(value.get("policy_revision"))
    rows = value.get("schedules")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        raise ValueError("native_maintenance_schedules_invalid")
    schedules = tuple(
        MaintenanceSchedule.from_mapping({**row, "policy_revision": revision}) for row in rows if isinstance(row, dict)
    )
    keys = {(item.tenant_id, item.project_id, item.schedule_id) for item in schedules}
    if len(keys) != len(rows):
        raise ValueError("native_maintenance_schedule_duplicate_or_invalid")
    return schedules


def maintenance_request(schedule, run_id):
    capabilities = [MAINTENANCE_CAPABILITY] + ([RETIREMENT_CAPABILITY] if schedule.retire_expired else [])
    tools = [MAINTENANCE_TOOL] + ([RETIREMENT_TOOL] if schedule.retire_expired else [])
    plan = ExecutionPlan.from_mapping(
        {
            "tenant_id": schedule.tenant_id,
            "plan_id": "native-maintenance-" + schedule.digest,
            "workflow_id": "native-maintenance-" + schedule.digest,
            "policy_version": schedule.policy_revision,
            "capabilities": capabilities,
            "metadata": {
                "hub_control_lease_required": True,
                "maintenance_policy_digest": schedule.digest,
                "workflow_rollout_scope": {"project_id": schedule.project_id, "tenant_id": schedule.tenant_id},
                "maintenance_schedule": schedule.to_mapping(),
                "auto_approval": "hub_preauthorization",
            },
            "nodes": [
                {
                    "id": "expire-results",
                    "task_kind": MAINTENANCE_TASK_KIND,
                    "required_capabilities": capabilities,
                    "allowed_tools": tools,
                    "side_effect_class": "idempotent_write",
                    "metadata": {"operation_name": MAINTENANCE_TOOL, "priority": "low"},
                    "budget": {"max_attempts": 1, "timeout_seconds": 60},
                }
            ],
            "budget": {"max_attempts": 1, "timeout_seconds": 120},
        }
    )
    inputs = {"batch_size": schedule.batch_size, "result_store_owner": dict(schedule.owner)}
    if schedule.retire_expired:
        inputs["retire_expired"] = True
    return NativeGraphRequest(
        plan, run_id, schedule.control_task_id, input_data=inputs, tenant_parallel_limit=1, worker_parallel_limit=1
    )
