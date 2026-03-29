from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, current_app

from agent.services.agent_health_monitor_service import AgentHealthMonitorService, get_agent_health_monitor_service
from agent.services.agent_registry_service import AgentRegistryService, get_agent_registry_service
from agent.services.autopilot_support_service import AutopilotSupportService, get_autopilot_support_service
from agent.services.automation_snapshot_service import AutomationSnapshotService, get_automation_snapshot_service
from agent.services.autopilot_runtime_service import AutopilotRuntimeService, get_autopilot_runtime_service
from agent.services.goal_service import GoalService, get_goal_service
from agent.services.lifecycle_service import GoalLifecycleService, get_goal_lifecycle_service
from agent.services.planning_service import PlanningService, get_planning_service
from agent.services.result_memory_service import ResultMemoryService, get_result_memory_service
from agent.services.system_contract_service import SystemContractService, get_system_contract_service
from agent.services.system_stats_service import SystemStatsService, get_system_stats_service
from agent.services.task_admin_service import TaskAdminService, get_task_admin_service
from agent.services.task_queue_service import TaskQueueService, get_task_queue_service
from agent.services.trigger_runtime_service import TriggerRuntimeService, get_trigger_runtime_service
from agent.services.verification_service import VerificationService, get_verification_service
from agent.services.worker_job_service import WorkerJobService, get_worker_job_service


@dataclass(frozen=True)
class CoreServiceRegistry:
    goal_service: GoalService
    goal_lifecycle_service: GoalLifecycleService
    planning_service: PlanningService
    task_queue_service: TaskQueueService
    task_admin_service: TaskAdminService
    autopilot_runtime_service: AutopilotRuntimeService
    autopilot_support_service: AutopilotSupportService
    trigger_runtime_service: TriggerRuntimeService
    automation_snapshot_service: AutomationSnapshotService
    verification_service: VerificationService
    worker_job_service: WorkerJobService
    result_memory_service: ResultMemoryService
    agent_registry_service: AgentRegistryService
    agent_health_monitor_service: AgentHealthMonitorService
    system_contract_service: SystemContractService
    system_stats_service: SystemStatsService


def build_core_service_registry() -> CoreServiceRegistry:
    return CoreServiceRegistry(
        goal_service=get_goal_service(),
        goal_lifecycle_service=get_goal_lifecycle_service(),
        planning_service=get_planning_service(),
        task_queue_service=get_task_queue_service(),
        task_admin_service=get_task_admin_service(),
        autopilot_runtime_service=get_autopilot_runtime_service(),
        autopilot_support_service=get_autopilot_support_service(),
        trigger_runtime_service=get_trigger_runtime_service(),
        automation_snapshot_service=get_automation_snapshot_service(),
        verification_service=get_verification_service(),
        worker_job_service=get_worker_job_service(),
        result_memory_service=get_result_memory_service(),
        agent_registry_service=get_agent_registry_service(),
        agent_health_monitor_service=get_agent_health_monitor_service(),
        system_contract_service=get_system_contract_service(),
        system_stats_service=get_system_stats_service(),
    )


def initialize_core_services(app: Flask) -> CoreServiceRegistry:
    registry = build_core_service_registry()
    app.extensions["core_services"] = registry
    return registry


def get_core_services(app: Flask | None = None) -> CoreServiceRegistry:
    target_app = app or current_app
    registry = target_app.extensions.get("core_services")
    if registry is None:
        registry = initialize_core_services(target_app)
    return registry
