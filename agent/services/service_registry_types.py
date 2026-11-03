"""Typed core definitions of the hub service registry (SRP/ISP/DIP).

Pure registry composition data: the sub-registry dataclasses with concrete
service types, the flat-access facade and the override projection math.
Framework bootstrap lives in ``service_registry_runtime``; this module
imports no Flask application machinery so the registry is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Any

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
