from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from flask import Flask, current_app

if TYPE_CHECKING:
    from agent.services.agent_health_monitor_service import AgentHealthMonitorService
    from agent.services.agent_registry_service import AgentRegistryService
    from agent.services.auto_planner_runtime_service import AutoPlannerRuntimeService
    from agent.services.autopilot_decision_service import AutopilotDecisionService
    from agent.services.autopilot_runtime_service import AutopilotRuntimeService
    from agent.services.autopilot_support_service import AutopilotSupportService
    from agent.services.automation_snapshot_service import AutomationSnapshotService
    from agent.services.config_read_model_service import ConfigReadModelService
    from agent.services.cost_aggregation_service import CostAggregationService
    from agent.services.goal_service import GoalService
    from agent.services.lifecycle_service import GoalLifecycleService
    from agent.services.log_service import LogService
    from agent.services.openai_compat_service import OpenAICompatService
    from agent.services.planning_service import PlanningService
    from agent.services.ingestion_service import IngestionService
    from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
    from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService
    from agent.services.rag_helper_index_service import RagHelperIndexService
    from agent.services.result_memory_service import ResultMemoryService
    from agent.services.scheduler_runtime_service import SchedulerRuntimeService
    from agent.services.system_contract_service import SystemContractService
    from agent.services.system_stats_service import SystemStatsService
    from agent.services.task_claim_service import TaskClaimService
    from agent.services.task_execution_tracking_service import TaskExecutionTrackingService
    from agent.services.task_management_service import TaskManagementService
    from agent.services.task_orchestration_service import TaskOrchestrationService
    from agent.services.task_admin_service import TaskAdminService
    from agent.services.task_query_service import TaskQueryService
    from agent.services.task_queue_service import TaskQueueService
    from agent.services.trigger_runtime_service import TriggerRuntimeService
    from agent.services.verification_service import VerificationService
    from agent.services.worker_contract_service import WorkerContractService
    from agent.services.worker_job_service import WorkerJobService


@dataclass(frozen=True)
class CoreServiceRegistry:
    goal_service: Any
    goal_lifecycle_service: Any
    planning_service: Any
    auto_planner_runtime_service: Any
    scheduler_runtime_service: Any
    config_read_model_service: Any
    task_queue_service: Any
    task_claim_service: Any
    task_execution_tracking_service: Any
    task_query_service: Any
    task_management_service: Any
    task_orchestration_service: Any
    task_admin_service: Any
    autopilot_runtime_service: Any
    autopilot_decision_service: Any
    autopilot_support_service: Any
    trigger_runtime_service: Any
    automation_snapshot_service: Any
    verification_service: Any
    worker_job_service: Any
    result_memory_service: Any
    agent_registry_service: Any
    agent_health_monitor_service: Any
    system_contract_service: Any
    system_stats_service: Any
    log_service: Any
    cost_aggregation_service: Any
    worker_contract_service: Any
    ingestion_service: Any
    knowledge_index_job_service: Any
    knowledge_index_retrieval_service: Any
    rag_helper_index_service: Any
    openai_compat_service: Any


def build_core_service_registry() -> CoreServiceRegistry:
    from agent.services.agent_health_monitor_service import get_agent_health_monitor_service
    from agent.services.agent_registry_service import get_agent_registry_service
    from agent.services.auto_planner_runtime_service import get_auto_planner_runtime_service
    from agent.services.autopilot_decision_service import get_autopilot_decision_service
    from agent.services.autopilot_runtime_service import get_autopilot_runtime_service
    from agent.services.autopilot_support_service import get_autopilot_support_service
    from agent.services.automation_snapshot_service import get_automation_snapshot_service
    from agent.services.config_read_model_service import get_config_read_model_service
    from agent.services.cost_aggregation_service import get_cost_aggregation_service
    from agent.services.goal_service import get_goal_service
    from agent.services.ingestion_service import get_ingestion_service
    from agent.services.knowledge_index_job_service import get_knowledge_index_job_service
    from agent.services.knowledge_index_retrieval_service import get_knowledge_index_retrieval_service
    from agent.services.lifecycle_service import get_goal_lifecycle_service
    from agent.services.log_service import get_log_service
    from agent.services.openai_compat_service import get_openai_compat_service
    from agent.services.planning_service import get_planning_service
    from agent.services.rag_helper_index_service import get_rag_helper_index_service
    from agent.services.result_memory_service import get_result_memory_service
    from agent.services.scheduler_runtime_service import get_scheduler_runtime_service
    from agent.services.system_contract_service import get_system_contract_service
    from agent.services.system_stats_service import get_system_stats_service
    from agent.services.task_claim_service import get_task_claim_service
    from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
    from agent.services.task_management_service import get_task_management_service
    from agent.services.task_orchestration_service import get_task_orchestration_service
    from agent.services.task_admin_service import get_task_admin_service
    from agent.services.task_query_service import get_task_query_service
    from agent.services.task_queue_service import get_task_queue_service
    from agent.services.trigger_runtime_service import get_trigger_runtime_service
    from agent.services.verification_service import get_verification_service
    from agent.services.worker_contract_service import get_worker_contract_service
    from agent.services.worker_job_service import get_worker_job_service

    return CoreServiceRegistry(
        goal_service=get_goal_service(),
        goal_lifecycle_service=get_goal_lifecycle_service(),
        planning_service=get_planning_service(),
        auto_planner_runtime_service=get_auto_planner_runtime_service(),
        scheduler_runtime_service=get_scheduler_runtime_service(),
        config_read_model_service=get_config_read_model_service(),
        task_queue_service=get_task_queue_service(),
        task_claim_service=get_task_claim_service(),
        task_execution_tracking_service=get_task_execution_tracking_service(),
        task_query_service=get_task_query_service(),
        task_management_service=get_task_management_service(),
        task_orchestration_service=get_task_orchestration_service(),
        task_admin_service=get_task_admin_service(),
        autopilot_runtime_service=get_autopilot_runtime_service(),
        autopilot_decision_service=get_autopilot_decision_service(),
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
        log_service=get_log_service(),
        cost_aggregation_service=get_cost_aggregation_service(),
        worker_contract_service=get_worker_contract_service(),
        ingestion_service=get_ingestion_service(),
        knowledge_index_job_service=get_knowledge_index_job_service(),
        knowledge_index_retrieval_service=get_knowledge_index_retrieval_service(),
        rag_helper_index_service=get_rag_helper_index_service(),
        openai_compat_service=get_openai_compat_service(),
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
