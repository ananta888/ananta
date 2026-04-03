from __future__ import annotations

from agent.models import (
    AgentRegisterRequest,
    AgentDirectoryEntryContract,
    AgentLivenessContract,
    ArtifactRagIndexRequest,
    ArtifactUploadRequest,
    AutoPlannerAnalyzeRequest,
    AutoPlannerConfigureRequest,
    AutoPlannerPlanRequest,
    CostSummaryContract,
    ConfigUpdateRequest,
    ContextBundleContract,
    RegistrationStateReadModel,
    ScheduledTaskCreateRequest,
    FollowupTaskCreateRequest,
    GoalCreateRequest,
    GoalPlanNodePatchRequest,
    GoalProvisionRequest,
    HubEventCatalogContract,
    HubEventContract,
    KnowledgeCollectionCreateRequest,
    KnowledgeCollectionIndexRequest,
    KnowledgeCollectionSearchRequest,
    SystemHealthReadModel,
    TaskStateMachineContract,
    TaskStatusContract,
    TaskAssignmentRequest,
    TaskClaimRequest,
    TaskCreateRequest,
    TaskDelegationRequest,
    TaskExecutionPolicyContract,
    TaskStepExecuteRequest,
    TaskStepExecuteResponse,
    TaskStepProposeRequest,
    TaskStepProposeResponse,
    TaskScopedStepExecuteResponse,
    TaskScopedStepProposeResponse,
    WorkerExecutionLimitsContract,
    WorkerExecutionContextContract,
    WorkerJobContract,
    WorkerRoutingDecisionContract,
    WorkerResultContract,
    TriggerConfigureRequest,
    TriggerTestRequest,
    TaskUpdateRequest,
)
from agent.services.hub_event_service import build_hub_event_catalog
from agent.services.task_state_machine_service import build_task_state_machine_contract, build_task_status_contract


class SystemContractService:
    """Central contract catalog to keep API/read-model schemas discoverable from one place."""

    def build_contract_catalog(self) -> dict:
        task_status_contract = build_task_status_contract()
        task_state_machine = build_task_state_machine_contract()
        schemas = {
            "agent_register_request": AgentRegisterRequest.model_json_schema(),
            "task_step_propose_request": TaskStepProposeRequest.model_json_schema(),
            "task_step_propose_response": TaskStepProposeResponse.model_json_schema(),
            "task_step_execute_request": TaskStepExecuteRequest.model_json_schema(),
            "task_step_execute_response": TaskStepExecuteResponse.model_json_schema(),
            "task_scoped_step_propose_response": TaskScopedStepProposeResponse.model_json_schema(),
            "task_scoped_step_execute_response": TaskScopedStepExecuteResponse.model_json_schema(),
            "cost_summary": CostSummaryContract.model_json_schema(),
            "task_execution_policy": TaskExecutionPolicyContract.model_json_schema(),
            "system_health": SystemHealthReadModel.model_json_schema(),
            "registration_state": RegistrationStateReadModel.model_json_schema(),
            "worker_execution_limits": WorkerExecutionLimitsContract.model_json_schema(),
            "agent_directory_entry": AgentDirectoryEntryContract.model_json_schema(),
            "agent_liveness": AgentLivenessContract.model_json_schema(),
            "worker_routing_decision": WorkerRoutingDecisionContract.model_json_schema(),
            "worker_execution_context": WorkerExecutionContextContract.model_json_schema(),
            "worker_job": WorkerJobContract.model_json_schema(),
            "worker_result": WorkerResultContract.model_json_schema(),
            "context_bundle": ContextBundleContract.model_json_schema(),
            "hub_event": HubEventContract.model_json_schema(),
            "hub_event_catalog": HubEventCatalogContract.model_json_schema(),
            "task_status_contract": TaskStatusContract.model_json_schema(),
            "task_state_machine": TaskStateMachineContract.model_json_schema(),
            "goal_create_request": GoalCreateRequest.model_json_schema(),
            "goal_plan_node_patch_request": GoalPlanNodePatchRequest.model_json_schema(),
            "goal_provision_request": GoalProvisionRequest.model_json_schema(),
            "artifact_upload_request": ArtifactUploadRequest.model_json_schema(),
            "artifact_rag_index_request": ArtifactRagIndexRequest.model_json_schema(),
            "knowledge_collection_create_request": KnowledgeCollectionCreateRequest.model_json_schema(),
            "knowledge_collection_index_request": KnowledgeCollectionIndexRequest.model_json_schema(),
            "knowledge_collection_search_request": KnowledgeCollectionSearchRequest.model_json_schema(),
            "trigger_configure_request": TriggerConfigureRequest.model_json_schema(),
            "trigger_test_request": TriggerTestRequest.model_json_schema(),
            "task_delegation_request": TaskDelegationRequest.model_json_schema(),
            "task_create_request": TaskCreateRequest.model_json_schema(),
            "task_update_request": TaskUpdateRequest.model_json_schema(),
            "task_assignment_request": TaskAssignmentRequest.model_json_schema(),
            "task_claim_request": TaskClaimRequest.model_json_schema(),
            "followup_task_create_request": FollowupTaskCreateRequest.model_json_schema(),
            "config_update_request": ConfigUpdateRequest.model_json_schema(),
            "scheduled_task_create_request": ScheduledTaskCreateRequest.model_json_schema(),
            "auto_planner_configure_request": AutoPlannerConfigureRequest.model_json_schema(),
            "auto_planner_plan_request": AutoPlannerPlanRequest.model_json_schema(),
            "auto_planner_analyze_request": AutoPlannerAnalyzeRequest.model_json_schema(),
        }
        return {
            "version": "v1",
            "schemas": schemas,
            "examples": {
                "hub_event": HubEventContract(
                    channel="task_history",
                    event_type="task_created",
                    timestamp=0.0,
                    actor="system",
                    details={"title": "Bootstrap task"},
                ).model_dump(),
                "hub_event_catalog": build_hub_event_catalog().model_dump(),
                "task_status_contract": task_status_contract.model_dump(),
                "task_state_machine": task_state_machine.model_dump(),
                "worker_execution_limits": WorkerExecutionLimitsContract().model_dump(),
                "agent_directory_entry": AgentDirectoryEntryContract(
                    name="worker-alpha",
                    url="http://worker-alpha:5000",
                ).model_dump(),
                "agent_liveness": AgentLivenessContract().model_dump(),
                "worker_routing_decision": WorkerRoutingDecisionContract(
                    worker_url="http://worker-research:5000",
                    selected_by_policy=True,
                    strategy="capability_quality_load_match",
                    reasons=["matched_capabilities:research,repo_research", "matched_roles:researcher"],
                    matched_capabilities=["research", "repo_research"],
                    matched_roles=["researcher"],
                    task_kind="research",
                    required_capabilities=["research", "repo_research"],
                ).model_dump(),
                "worker_execution_context": WorkerExecutionContextContract(
                    instructions="Investigate the repository and produce a cited research summary.",
                    context_bundle_id="bundle-research-1",
                    context={"context_text": "Repository summary", "chunks": [{"id": "chunk-1"}], "token_estimate": 256},
                    context_policy={"mode": "standard", "max_chunks": 8},
                    allowed_tools=["bash", "rg"],
                    expected_output_schema={"type": "object", "required": ["summary", "sources"]},
                    routing=WorkerRoutingDecisionContract(
                        worker_url="http://worker-research:5000",
                        selected_by_policy=True,
                        strategy="capability_quality_load_match",
                        matched_capabilities=["research", "repo_research"],
                        matched_roles=["researcher"],
                        task_kind="research",
                        required_capabilities=["research", "repo_research"],
                    ),
                ).model_dump(exclude_none=True),
                "task_step_propose_response": TaskStepProposeResponse(
                    reason="Run unit tests first",
                    command="pytest -q",
                    raw='{"reason":"Run unit tests first","command":"pytest -q"}',
                ).model_dump(),
                "task_step_execute_response": TaskStepExecuteResponse(
                    output="ok",
                    exit_code=0,
                    task_id="task-1",
                    status="completed",
                    retry_history=[],
                ).model_dump(),
                "task_scoped_step_propose_response": TaskScopedStepProposeResponse(
                    status="proposing",
                    reason="Run unit tests first",
                    command="pytest -q",
                    raw='{"reason":"Run unit tests first","command":"pytest -q"}',
                    backend="aider",
                    routing={"task_kind": "coding", "effective_backend": "aider", "reason": "adaptive_default"},
                    cli_result={"returncode": 0, "latency_ms": 120, "stderr_preview": ""},
                    worker_context={"context_bundle_id": "bundle-1", "allowed_tools": ["bash"], "context_chunk_count": 1},
                    review={"required": False, "status": "not_required"},
                ).model_dump(exclude_none=True),
                "task_scoped_step_execute_response": TaskScopedStepExecuteResponse(
                    output="ok",
                    exit_code=0,
                    task_id="task-1",
                    status="completed",
                    retry_history=[],
                    cost_summary=CostSummaryContract(provider="aider", model="gpt-4o-mini", tokens_total=42),
                    retries_used=0,
                    failure_type="success",
                    execution_policy=TaskExecutionPolicyContract(
                        timeout_seconds=60,
                        retries=0,
                        retry_delay_seconds=1,
                        source="task_execute",
                    ),
                ).model_dump(exclude_none=True),
                "cost_summary": CostSummaryContract(
                    provider="aider",
                    model="gpt-4o-mini",
                    task_kind="coding",
                    tokens_total=42,
                    cost_units=0.12,
                    latency_ms=320,
                    pricing_source="default_table",
                ).model_dump(exclude_none=True),
            },
        }

    def build_openapi_document(self) -> dict:
        catalog = self.build_contract_catalog()
        schemas = catalog["schemas"]
        return {
            "openapi": "3.1.0",
            "info": {
                "title": "Ananta Agent API",
                "version": "1.0.0",
                "description": "Generated from the central SystemContractService contract catalog.",
            },
            "paths": {
                "/step/propose": {
                    "post": self._operation(
                        summary="Propose direct next step",
                        request_schema="task_step_propose_request",
                        response_schema="task_step_propose_response",
                    )
                },
                "/step/execute": {
                    "post": self._operation(
                        summary="Execute direct step",
                        request_schema="task_step_execute_request",
                        response_schema="task_step_execute_response",
                    )
                },
                "/tasks/{tid}/step/propose": {
                    "post": self._operation(
                        summary="Propose task-scoped next step",
                        request_schema="task_step_propose_request",
                        response_schema="task_scoped_step_propose_response",
                        path_parameters=[
                            {
                                "name": "tid",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Task identifier",
                            }
                        ],
                    )
                },
                "/tasks/{tid}/step/execute": {
                    "post": self._operation(
                        summary="Execute task-scoped step",
                        request_schema="task_step_execute_request",
                        response_schema="task_scoped_step_execute_response",
                        path_parameters=[
                            {
                                "name": "tid",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                                "description": "Task identifier",
                            }
                        ],
                    )
                },
                "/api/system/contracts": {
                    "get": {
                        "summary": "Read central contract catalog",
                        "responses": {
                            "200": {
                                "description": "Contract catalog",
                                "content": {
                                    "application/json": {
                                        "example": {
                                            "status": "success",
                                            "data": {
                                                "version": catalog["version"],
                                                "schemas": "<component schemas>",
                                                "examples": catalog["examples"],
                                            },
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
                "/api/system/openapi.json": {
                    "get": {
                        "summary": "Read generated OpenAPI document",
                        "responses": {
                            "200": {
                                "description": "OpenAPI document",
                                "content": {
                                    "application/json": {
                                        "example": {
                                            "openapi": "3.1.0",
                                            "info": {"title": "Ananta Agent API", "version": "1.0.0"},
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
            },
            "components": {"schemas": schemas},
        }

    def _operation(
        self,
        *,
        summary: str,
        request_schema: str,
        response_schema: str,
        path_parameters: list[dict] | None = None,
    ) -> dict:
        operation = {
            "summary": summary,
            "responses": {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{response_schema}"}
                        }
                    },
                }
            },
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{request_schema}"}
                    }
                },
            },
        }
        if path_parameters:
            operation["parameters"] = path_parameters
        return operation


system_contract_service = SystemContractService()


def get_system_contract_service() -> SystemContractService:
    return system_contract_service
