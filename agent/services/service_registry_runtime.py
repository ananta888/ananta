"""Flask-side composition of the hub service registry.

Builds the typed registry from the service singletons, stores it on the app
and exposes the accessors. Kept out of the registry types so the core
definitions stay framework-free (DIP).
"""

from __future__ import annotations

from typing import Any

from flask import Flask, current_app

from agent.services.service_registry_types import (
    _CORE_SERVICE_FIELD_OWNERS,
    _CORE_SERVICE_OVERRIDES_EXTENSION,
    CoreServiceRegistry,
    GovernanceServices,
    IntegrationServices,
    KnowledgeServices,
    RuntimeServices,
    TaskServices,
    _apply_core_service_overrides,
)


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
