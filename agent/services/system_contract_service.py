from __future__ import annotations

from agent.models import (
    AgentRegisterRequest,
    GoalCreateRequest,
    SystemHealthReadModel,
    TaskExecutionPolicyContract,
    TaskStepExecuteRequest,
    TaskStepProposeRequest,
    TriggerConfigureRequest,
)


class SystemContractService:
    """Central contract catalog to keep API/read-model schemas discoverable from one place."""

    def build_contract_catalog(self) -> dict:
        return {
            "version": "v1",
            "schemas": {
                "agent_register_request": AgentRegisterRequest.model_json_schema(),
                "task_step_propose_request": TaskStepProposeRequest.model_json_schema(),
                "task_step_execute_request": TaskStepExecuteRequest.model_json_schema(),
                "task_execution_policy": TaskExecutionPolicyContract.model_json_schema(),
                "system_health": SystemHealthReadModel.model_json_schema(),
                "goal_create_request": GoalCreateRequest.model_json_schema(),
                "trigger_configure_request": TriggerConfigureRequest.model_json_schema(),
            },
        }


system_contract_service = SystemContractService()


def get_system_contract_service() -> SystemContractService:
    return system_contract_service
