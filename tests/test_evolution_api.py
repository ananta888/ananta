from agent.db_models import TaskDB
from agent.repository import task_repo
from agent.services.evolution import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionEngine,
    EvolutionProposal,
    EvolutionResult,
    ValidationResult,
)
from agent.services.evolution.registry import get_evolution_provider_registry


class ApiEvolutionEngine(EvolutionEngine):
    provider_name = "api-evolution"
    capabilities = [EvolutionCapability.ANALYZE, EvolutionCapability.PROPOSE, EvolutionCapability.VALIDATE]

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        return EvolutionResult(
            provider_name=self.provider_name,
            summary=f"API analysis for {context.task_id}",
            proposals=[
                EvolutionProposal(
                    title="Review failed task",
                    description="Create a reviewable proposal from the API trigger.",
                    risk_level="low",
                    confidence=0.7,
                )
            ],
        )

    def validate(self, context: EvolutionContext, proposal: EvolutionProposal) -> ValidationResult:
        return ValidationResult(proposal_id=proposal.proposal_id, status="passed", valid=True)


def test_evolution_provider_discovery_endpoint(client, app, admin_auth_header):
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(ApiEvolutionEngine(), default=True)
    try:
        response = client.get("/evolution/providers", headers=admin_auth_header)
        detail_response = client.get("/evolution/providers/api-evolution", headers=admin_auth_header)
    finally:
        registry.clear()

    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["providers"][0]["provider_name"] == "api-evolution"
    assert payload["providers"][0]["default"] is True
    assert payload["health"]["status"] == "available"
    assert payload["config"]["analyze_only"] is True
    assert payload["config"]["apply_allowed"] is False

    assert detail_response.status_code == 200
    assert detail_response.json["data"]["providers"][0]["provider_name"] == "api-evolution"


def test_task_evolution_analyze_and_read_model_endpoints(client, app, admin_auth_header):
    task_repo.save(
        TaskDB(
            id="T-EVO-API",
            title="API evolution task",
            description="Needs an improvement proposal.",
            status="failed",
            goal_trace_id="trace-api",
        )
    )
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(ApiEvolutionEngine(), default=True)
    try:
        analyze_response = client.post(
            "/tasks/T-EVO-API/evolution/analyze",
            headers=admin_auth_header,
            json={"trigger_type": "manual", "reason": "manual check"},
        )
        read_response = client.get("/tasks/T-EVO-API/evolution", headers=admin_auth_header)
    finally:
        registry.clear()

    assert analyze_response.status_code == 200
    analyze_payload = analyze_response.json["data"]
    assert analyze_payload["provider_name"] == "api-evolution"
    assert analyze_payload["status"] == "completed"
    assert len(analyze_payload["proposal_ids"]) == 1

    assert read_response.status_code == 200
    read_payload = read_response.json["data"]
    assert read_payload["task_id"] == "T-EVO-API"
    assert read_payload["run_count"] == 1
    assert read_payload["proposal_count"] == 1
    assert read_payload["runs"][0]["trigger_type"] == "manual"
    assert read_payload["proposals"][0]["title"] == "Review failed task"


def test_task_evolution_validate_endpoint(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-VALIDATE", title="Validate proposal", status="failed"))
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(ApiEvolutionEngine(), default=True)
    try:
        analyze_response = client.post(
            "/tasks/T-EVO-VALIDATE/evolution/analyze",
            headers=admin_auth_header,
            json={"trigger_type": "manual"},
        )
        proposal_id = analyze_response.json["data"]["proposal_ids"][0]
        validate_response = client.post(
            f"/tasks/T-EVO-VALIDATE/evolution/proposals/{proposal_id}/validate",
            headers=admin_auth_header,
            json={},
        )
    finally:
        registry.clear()

    assert validate_response.status_code == 200
    assert validate_response.json["data"]["status"] == "passed"
    assert validate_response.json["data"]["valid"] is True


def test_task_evolution_analyze_rejects_invalid_trigger(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-BAD-TRIGGER", title="Bad trigger"))

    response = client.post(
        "/tasks/T-EVO-BAD-TRIGGER/evolution/analyze",
        headers=admin_auth_header,
        json={"trigger_type": "unknown"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "invalid_trigger_type"
