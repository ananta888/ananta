from __future__ import annotations

from agent.models import (
    AgentRegisterRequest,
    AgentDirectoryEntryContract,
    AgentLivenessContract,
    AutoPlannerAnalyzeRequest,
    AutoPlannerConfigureRequest,
    AutoPlannerPlanRequest,
    ConfigUpdateRequest,
    ContextBundleContract,
    RegistrationStateReadModel,
    ScheduledTaskCreateRequest,
    FollowupTaskCreateRequest,
    GoalCreateRequest,
    GoalPlanNodePatchRequest,
    GoalProvisionRequest,
    SystemHealthReadModel,
    TaskStateMachineContract,
    TaskStatusContract,
    TaskAssignmentRequest,
    TaskClaimRequest,
    TaskCreateRequest,
    TaskDelegationRequest,
    TaskExecutionPolicyContract,
    TaskStepExecuteRequest,
    TaskStepProposeRequest,
    WorkerExecutionLimitsContract,
    WorkerExecutionContextContract,
    WorkerJobContract,
    WorkerRoutingDecisionContract,
    WorkerResultContract,
    TriggerConfigureRequest,
    TriggerTestRequest,
    TaskUpdateRequest,
)
from agent.services.task_state_machine_service import build_task_state_machine_contract, build_task_status_contract


class SystemContractService:
    """Central contract catalog to keep API/read-model schemas discoverable from one place."""

    def build_contract_catalog(self) -> dict:
        task_status_contract = build_task_status_contract()
        task_state_machine = build_task_state_machine_contract()
        schemas = {
            "agent_register_request": AgentRegisterRequest.model_json_schema(),
            "task_step_propose_request": TaskStepProposeRequest.model_json_schema(),
            "task_step_execute_request": TaskStepExecuteRequest.model_json_schema(),
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
            "task_status_contract": TaskStatusContract.model_json_schema(),
            "task_state_machine": TaskStateMachineContract.model_json_schema(),
            "goal_create_request": GoalCreateRequest.model_json_schema(),
            "goal_plan_node_patch_request": GoalPlanNodePatchRequest.model_json_schema(),
            "goal_provision_request": GoalProvisionRequest.model_json_schema(),
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
                "task_status_contract": task_status_contract.model_dump(),
                "task_state_machine": task_state_machine.model_dump(),
                "worker_execution_limits": WorkerExecutionLimitsContract().model_dump(),
                "agent_liveness": AgentLivenessContract().model_dump(),
                "worker_routing_decision": WorkerRoutingDecisionContract().model_dump(),
            },
        }


system_contract_service = SystemContractService()


def get_system_contract_service() -> SystemContractService:
    return system_contract_service
