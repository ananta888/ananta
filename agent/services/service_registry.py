from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Any

from flask import Flask, current_app

if TYPE_CHECKING:
    from agent.ports.evidence_identity import EvidenceIdentityRegistryPort
    from agent.services.agent_health_monitor_service import AgentHealthMonitorService
    from agent.services.agent_registry_service import AgentRegistryService
    from agent.services.auto_planner_runtime_service import AutoPlannerRuntimeService
    from agent.services.automation_snapshot_service import AutomationSnapshotService
    from agent.services.autopilot_decision_service import AutopilotDecisionService
    from agent.services.autopilot_runtime_service import AutopilotRuntimeService
    from agent.services.autopilot_support_service import AutopilotSupportService
    from agent.services.citation_verification_service import CitationVerificationService
    from agent.services.config_read_model_service import ConfigReadModelService
    from agent.services.cost_aggregation_service import CostAggregationService
    from agent.services.evolution_service import EvolutionService
    from agent.services.goal_service import GoalService
    from agent.services.hint_routing_service import HintRoutingService
    from agent.services.hub_evidence_gate_service import HubEvidenceGateService
    from agent.services.ingestion_service import IngestionService
    from agent.services.integration_registry_service import IntegrationRegistryService
    from agent.services.knowledge_index_job_service import KnowledgeIndexJobService
    from agent.services.knowledge_index_retrieval_service import KnowledgeIndexRetrievalService
    from agent.services.lifecycle_service import GoalLifecycleService
    from agent.services.log_service import LogService
    from agent.services.mcp_registry_service import MCPToolSpec
    from agent.services.memory_tree_retrieval_service import MemoryTreeRetrievalService
    from agent.services.memory_tree_store_service import MemoryTreeStoreService
    from agent.services.memory_tree_summary_service import MemoryTreeSummaryService
    from agent.services.memory_vault_export_service import MemoryVaultExportService
    from agent.services.openai_compat_service import OpenAICompatService
    from agent.services.planning_service import PlanningService
    from agent.services.rag_helper_index_service import RagHelperIndexService
    from agent.services.rag_service import RagService
    from agent.services.rate_limit_service import RateLimitService
    from agent.services.result_memory_service import ResultMemoryService
    from agent.services.scheduler_runtime_service import SchedulerRuntimeService
    from agent.services.source_catalog_service import SourceCatalogService
    from agent.services.system_contract_service import SystemContractService
    from agent.services.system_stats_service import SystemStatsService
    from agent.services.task_admin_service import TaskAdminService
    from agent.services.task_claim_service import TaskClaimService
    from agent.services.task_execution_service import TaskExecutionService
    from agent.services.task_execution_tracking_service import TaskExecutionTrackingService
    from agent.services.task_handler_registry import TaskHandlerRegistry
    from agent.services.task_management_service import TaskManagementService
    from agent.services.task_orchestration_service import TaskOrchestrationService
    from agent.services.task_query_service import TaskQueryService
    from agent.services.task_queue_service import TaskQueueService
    from agent.services.task_runtime_service import TaskRuntimeService
    from agent.services.task_scoped_execution_service import TaskScopedExecutionService
    from agent.services.tool_output_compaction_service import ToolOutputCompactionService
    from agent.services.tool_run_catalog_service import ToolRunCatalogService
    from agent.services.trigger_runtime_service import TriggerRuntimeService
    from agent.services.verification_service import VerificationService
    from agent.services.worker_contract_service import WorkerContractService
    from agent.services.worker_job_service import WorkerJobService


@dataclass(frozen=True)
class TaskServices:
    """Task-Lifecycle: Queue, Claim, Execution, Query, Management, Orchestration."""

    task_handler_registry: TaskHandlerRegistry
    task_queue_service: TaskQueueService
    task_runtime_service: TaskRuntimeService
    task_claim_service: TaskClaimService
    task_execution_tracking_service: TaskExecutionTrackingService
    task_execution_service: TaskExecutionService
    task_scoped_execution_service: TaskScopedExecutionService
    task_query_service: TaskQueryService
    task_management_service: TaskManagementService
    task_orchestration_service: TaskOrchestrationService
    task_admin_service: TaskAdminService


@dataclass(frozen=True)
class GovernanceServices:
    """Goals, Planung, Verification, Contracts, Evolution."""

    goal_service: GoalService
    goal_lifecycle_service: GoalLifecycleService
    planning_service: PlanningService
    auto_planner_runtime_service: AutoPlannerRuntimeService
    verification_service: VerificationService
    worker_contract_service: WorkerContractService
    system_contract_service: SystemContractService
    evolution_service: EvolutionService
    evidence_identity_registry_service: EvidenceIdentityRegistryPort | None = None
    evidence_gate_service: HubEvidenceGateService | None = None


@dataclass(frozen=True)
class KnowledgeServices:
    """RAG, Ingestion, Knowledge-Index, Result-Memory, Memory-Tree, Tool-Output-Compaction."""

    rag_service: RagService
    rag_helper_index_service: RagHelperIndexService
    ingestion_service: IngestionService
    knowledge_index_job_service: KnowledgeIndexJobService
    knowledge_index_retrieval_service: KnowledgeIndexRetrievalService
    result_memory_service: ResultMemoryService
    source_catalog_service: SourceCatalogService | None = None
    tool_run_catalog_service: ToolRunCatalogService | None = None
    citation_verification_service: CitationVerificationService | None = None
    # OHA-002/010/011: planned slots — wired once services are implemented
    memory_tree_store_service: MemoryTreeStoreService | None = None
    memory_tree_summary_service: MemoryTreeSummaryService | None = None
    memory_tree_retrieval_service: MemoryTreeRetrievalService | None = None
    tool_output_compaction_service: ToolOutputCompactionService | None = None
    memory_vault_export_service: MemoryVaultExportService | None = None


@dataclass(frozen=True)
class IntegrationServices:
    """LLM/MCP/OpenAI-Bridge, Worker-Jobs, Agent-Registry, Hint-Routing."""

    mcp_registry_service: MCPToolSpec
    integration_registry_service: IntegrationRegistryService
    openai_compat_service: OpenAICompatService
    worker_job_service: WorkerJobService
    agent_registry_service: AgentRegistryService
    agent_health_monitor_service: AgentHealthMonitorService
    # OHA-002: planned slot — wired once HintRoutingService is implemented
    hint_routing_service: HintRoutingService | None = None


@dataclass(frozen=True)
class RuntimeServices:
    """Autopilot, Trigger, Scheduler, Config, Stats, Logs, Cost, Rate-Limit."""

    autopilot_runtime_service: AutopilotRuntimeService
    autopilot_decision_service: AutopilotDecisionService
    autopilot_support_service: AutopilotSupportService
    trigger_runtime_service: TriggerRuntimeService
    scheduler_runtime_service: SchedulerRuntimeService
    automation_snapshot_service: AutomationSnapshotService
    config_read_model_service: ConfigReadModelService
    cost_aggregation_service: CostAggregationService
    system_stats_service: SystemStatsService
    log_service: LogService
    rate_limit_service: RateLimitService


_SUB_REGISTRY_FIELDS = ("tasks", "governance", "knowledge", "integrations", "runtime")
_CORE_SERVICE_OVERRIDES_EXTENSION = "core_service_overrides"


@dataclass(frozen=True)
class CoreServiceRegistry:
    """Aggregiert die fachlichen Teil-Registries.

    Flacher Zugriff (``registry.goal_service``) bleibt aus Kompatibilitätsgründen
    über ``__getattr__`` an die passende Teil-Registry delegiert.
    """

    tasks: TaskServices
    governance: GovernanceServices
    knowledge: KnowledgeServices
    integrations: IntegrationServices
    runtime: RuntimeServices

    def __getattr__(self, name: str) -> Any:
        for sub_name in _SUB_REGISTRY_FIELDS:
            try:
                sub = object.__getattribute__(self, sub_name)
            except AttributeError:
                continue
            sub_fields = getattr(type(sub), "__dataclass_fields__", {})
            if name in sub_fields:
                return getattr(sub, name)
        raise AttributeError(f"CoreServiceRegistry has no attribute {name!r}")


def _build_core_service_field_owners() -> dict[str, str]:
    """Map every unique flat service field to its owning sub-registry."""

    owners: dict[str, str] = {}
    for sub_name, sub_cls in (
        ("tasks", TaskServices),
        ("governance", GovernanceServices),
        ("knowledge", KnowledgeServices),
        ("integrations", IntegrationServices),
        ("runtime", RuntimeServices),
    ):
        for field in fields(sub_cls):
            if field.name in owners:
                raise RuntimeError(
                    "CoreServiceRegistry field collision: "
                    f"{field.name!r} lebt sowohl in {owners[field.name]!r} "
                    f"als auch in {sub_name!r}"
                )
            owners[field.name] = sub_name
    return owners


_CORE_SERVICE_FIELD_OWNERS = _build_core_service_field_owners()


def _apply_core_service_overrides(
    registry: CoreServiceRegistry,
    overrides: dict[str, Any],
) -> CoreServiceRegistry:
    """Return an immutable registry projection with app-local overrides."""

    if not overrides:
        return registry
    grouped: dict[str, dict[str, Any]] = {}
    for service_field_name, service in overrides.items():
        owner = _CORE_SERVICE_FIELD_OWNERS.get(service_field_name)
        if owner is None:
            raise ValueError("core_service_override_field_unknown")
        grouped.setdefault(owner, {})[service_field_name] = service
    projected = registry
    for owner, owner_overrides in grouped.items():
        projected = replace(
            projected,
            **{
                owner: replace(
                    getattr(projected, owner),
                    **owner_overrides,
                )
            },
        )
    return projected


def register_core_service_override(
    app: Flask,
    *,
    service_field_name: str,
    service: Any,
) -> None:
    """Register one bootstrap-only app service override by flat field name.

    Registration updates an already composed registry immediately.  The stored
    override is also applied again when the normal late bootstrap rebuilds the
    registry, so domain compositions do not depend on bootstrap ordering.  It
    is intentionally not a runtime mutation API and therefore uses no locking.
    """

    normalized_field_name = str(service_field_name or "").strip()
    if normalized_field_name not in _CORE_SERVICE_FIELD_OWNERS:
        raise ValueError("core_service_override_field_unknown")
    overrides = dict(app.extensions.get(_CORE_SERVICE_OVERRIDES_EXTENSION) or {})
    overrides[normalized_field_name] = service
    existing_registry = app.extensions.get("core_services")
    projected_registry = (
        _apply_core_service_overrides(existing_registry, overrides) if existing_registry is not None else None
    )
    app.extensions[_CORE_SERVICE_OVERRIDES_EXTENSION] = overrides
    if projected_registry is not None:
        app.extensions["core_services"] = projected_registry


def build_core_service_registry(app: Flask | None = None) -> CoreServiceRegistry:
    from agent.services.agent_health_monitor_service import get_agent_health_monitor_service
    from agent.services.agent_registry_service import get_agent_registry_service
    from agent.services.auto_planner_runtime_service import get_auto_planner_runtime_service
    from agent.services.automation_snapshot_service import get_automation_snapshot_service
    from agent.services.autopilot_decision_service import get_autopilot_decision_service
    from agent.services.autopilot_runtime_service import get_autopilot_runtime_service
    from agent.services.autopilot_support_service import get_autopilot_support_service
    from agent.services.citation_verification_service import get_citation_verification_service
    from agent.services.config_read_model_service import get_config_read_model_service
    from agent.services.cost_aggregation_service import get_cost_aggregation_service
    from agent.services.evolution_service import get_evolution_service
    from agent.services.goal_service import get_goal_service
    from agent.services.hint_routing_service import get_hint_routing_service
    from agent.services.hub_evidence_gate_service import get_hub_evidence_gate_service
    from agent.services.hub_evidence_registry_service import (
        get_hub_evidence_registry_service,
    )
    from agent.services.ingestion_service import get_ingestion_service
    from agent.services.integration_registry_service import get_integration_registry_service
    from agent.services.knowledge_index_job_service import get_knowledge_index_job_service
    from agent.services.knowledge_index_retrieval_service import get_knowledge_index_retrieval_service
    from agent.services.lifecycle_service import get_goal_lifecycle_service
    from agent.services.log_service import get_log_service
    from agent.services.mcp_registry_service import get_mcp_registry_service
    from agent.services.memory_tree_retrieval_service import get_memory_tree_retrieval_service
    from agent.services.memory_tree_store_service import get_memory_tree_store_service
    from agent.services.memory_tree_summary_service import get_memory_tree_summary_service
    from agent.services.memory_vault_export_service import get_memory_vault_export_service
    from agent.services.openai_compat_service import get_openai_compat_service
    from agent.services.planning_service import get_planning_service
    from agent.services.rag_helper_index_service import get_rag_helper_index_service
    from agent.services.rag_service import get_rag_service
    from agent.services.rate_limit_service import get_rate_limit_service
    from agent.services.result_memory_service import get_result_memory_service
    from agent.services.scheduler_runtime_service import get_scheduler_runtime_service
    from agent.services.source_catalog_service import get_source_catalog_service
    from agent.services.system_contract_service import get_system_contract_service
    from agent.services.system_stats_service import get_system_stats_service
    from agent.services.task_admin_service import get_task_admin_service
    from agent.services.task_claim_service import get_task_claim_service
    from agent.services.task_execution_service import get_task_execution_service
    from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
    from agent.services.task_handler_registry import get_task_handler_registry
    from agent.services.task_management_service import get_task_management_service
    from agent.services.task_orchestration_service import get_task_orchestration_service
    from agent.services.task_query_service import get_task_query_service
    from agent.services.task_queue_service import get_task_queue_service
    from agent.services.task_runtime_service import get_task_runtime_service
    from agent.services.task_scoped_execution_service import get_task_scoped_execution_service
    from agent.services.tool_output_compaction_service import _build_from_config as get_tool_output_compaction_service
    from agent.services.tool_run_catalog_service import get_tool_run_catalog_service
    from agent.services.trigger_runtime_service import get_trigger_runtime_service
    from agent.services.verification_service import get_verification_service
    from agent.services.worker_contract_service import get_worker_contract_service
    from agent.services.worker_job_service import get_worker_job_service

    tasks = TaskServices(
        task_handler_registry=get_task_handler_registry(app),
        task_queue_service=get_task_queue_service(),
        task_runtime_service=get_task_runtime_service(),
        task_claim_service=get_task_claim_service(),
        task_execution_tracking_service=get_task_execution_tracking_service(),
        task_execution_service=get_task_execution_service(),
        task_scoped_execution_service=get_task_scoped_execution_service(),
        task_query_service=get_task_query_service(),
        task_management_service=get_task_management_service(),
        task_orchestration_service=get_task_orchestration_service(),
        task_admin_service=get_task_admin_service(),
    )
    governance = GovernanceServices(
        goal_service=get_goal_service(),
        goal_lifecycle_service=get_goal_lifecycle_service(),
        planning_service=get_planning_service(),
        auto_planner_runtime_service=get_auto_planner_runtime_service(),
        verification_service=get_verification_service(),
        worker_contract_service=get_worker_contract_service(),
        system_contract_service=get_system_contract_service(),
        evolution_service=get_evolution_service(),
        evidence_identity_registry_service=get_hub_evidence_registry_service(),
        evidence_gate_service=get_hub_evidence_gate_service(),
    )
    knowledge = KnowledgeServices(
        rag_service=get_rag_service(),
        rag_helper_index_service=get_rag_helper_index_service(),
        ingestion_service=get_ingestion_service(),
        knowledge_index_job_service=get_knowledge_index_job_service(),
        knowledge_index_retrieval_service=get_knowledge_index_retrieval_service(),
        result_memory_service=get_result_memory_service(),
        source_catalog_service=get_source_catalog_service(),
        tool_run_catalog_service=get_tool_run_catalog_service(),
        citation_verification_service=get_citation_verification_service(),
        memory_tree_store_service=get_memory_tree_store_service(),
        memory_tree_summary_service=get_memory_tree_summary_service(),
        memory_tree_retrieval_service=get_memory_tree_retrieval_service(),
        tool_output_compaction_service=get_tool_output_compaction_service({}),
        memory_vault_export_service=get_memory_vault_export_service(),
    )
    integrations = IntegrationServices(
        mcp_registry_service=get_mcp_registry_service(),
        integration_registry_service=get_integration_registry_service(),
        openai_compat_service=get_openai_compat_service(),
        worker_job_service=get_worker_job_service(),
        agent_registry_service=get_agent_registry_service(),
        agent_health_monitor_service=get_agent_health_monitor_service(),
        hint_routing_service=get_hint_routing_service(),
    )
    runtime = RuntimeServices(
        autopilot_runtime_service=get_autopilot_runtime_service(),
        autopilot_decision_service=get_autopilot_decision_service(),
        autopilot_support_service=get_autopilot_support_service(),
        trigger_runtime_service=get_trigger_runtime_service(),
        scheduler_runtime_service=get_scheduler_runtime_service(),
        automation_snapshot_service=get_automation_snapshot_service(),
        config_read_model_service=get_config_read_model_service(),
        cost_aggregation_service=get_cost_aggregation_service(),
        system_stats_service=get_system_stats_service(),
        log_service=get_log_service(),
        rate_limit_service=get_rate_limit_service(),
    )

    return CoreServiceRegistry(
        tasks=tasks,
        governance=governance,
        knowledge=knowledge,
        integrations=integrations,
        runtime=runtime,
    )


def initialize_core_services(app: Flask) -> CoreServiceRegistry:
    registry = build_core_service_registry(app)
    registry = _apply_core_service_overrides(
        registry,
        dict(app.extensions.get(_CORE_SERVICE_OVERRIDES_EXTENSION) or {}),
    )
    app.extensions["core_services"] = registry
    return registry


def get_core_services(app: Flask | None = None) -> CoreServiceRegistry:
    target_app = app or current_app
    registry = target_app.extensions.get("core_services")
    if registry is None:
        registry = initialize_core_services(target_app)
    return registry


def get_task_services(app: Flask | None = None) -> TaskServices:
    return get_core_services(app).tasks


def get_governance_services(app: Flask | None = None) -> GovernanceServices:
    return get_core_services(app).governance


def get_knowledge_services(app: Flask | None = None) -> KnowledgeServices:
    return get_core_services(app).knowledge


def get_integration_services(app: Flask | None = None) -> IntegrationServices:
    return get_core_services(app).integrations


def get_runtime_services(app: Flask | None = None) -> RuntimeServices:
    return get_core_services(app).runtime
