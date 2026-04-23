from agent.db_models import TaskDB
from agent.repository import task_repo
from agent.services.evolution import (
    ApplyResult,
    EvolutionCapability,
    EvolutionContext,
    EvolutionEngine,
    EvolutionProposal,
    EvolutionResult,
    ValidationResult,
)
from agent.services.evolution.registry import get_evolution_provider_registry
from plugins.evolver_adapter.adapter import EvolverAdapter, EvolverTimeoutError


class ApiEvolutionEngine(EvolutionEngine):
    provider_name = "api-evolution"
    capabilities = [
        EvolutionCapability.ANALYZE,
        EvolutionCapability.PROPOSE,
        EvolutionCapability.VALIDATE,
        EvolutionCapability.APPLY,
    ]

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        return EvolutionResult(
            provider_name=self.provider_name,
            summary=f"API analysis for {context.task_id}",
            provider_metadata={"source": "api-test", "evolver_status": "completed"},
            proposals=[
                EvolutionProposal(
                    title="Review failed task",
                    description="Create a reviewable proposal from the API trigger.",
                    risk_level="low",
                    confidence=0.7,
                    provider_metadata={"source": "api-test", "evolver_kind": "gene"},
                )
            ],
        )

    def validate(self, context: EvolutionContext, proposal: EvolutionProposal) -> ValidationResult:
        return ValidationResult(proposal_id=proposal.proposal_id, status="passed", valid=True)

    def apply(self, context: EvolutionContext, proposal: EvolutionProposal) -> ApplyResult:
        return ApplyResult(proposal_id=proposal.proposal_id, status="prepared", applied=False)


class TimeoutEvolverTransport:
    def analyze(self, payload: dict) -> dict:
        raise EvolverTimeoutError()

    def health(self) -> dict:
        raise EvolverTimeoutError()


class AnalyzeOnlyEvolverTransport:
    def analyze(self, payload: dict) -> dict:
        return {
            "status": "completed",
            "summary": "Analyze-only result",
            "proposals": [{"id": "evolver-proposal", "title": "Review only", "risk": "low"}],
        }

    def health(self) -> dict:
        return {"status": "available"}


def test_evolution_provider_discovery_endpoint(client, app, admin_auth_header):
    old_cfg = dict(app.config.get("AGENT_CONFIG") or {})
    cfg = dict(app.config.get("AGENT_CONFIG") or {})
    cfg["evolution"] = {
        **dict(cfg.get("evolution") or {}),
        "provider_overrides": {
            "api-evolution": {
                "force_analyze_only": True,
                "bearer_token": "secret-token",
                "headers": {"Authorization": "Bearer secret"},
            }
        },
    }
    app.config["AGENT_CONFIG"] = cfg
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(ApiEvolutionEngine(), default=True)
    try:
        response = client.get("/evolution/providers", headers=admin_auth_header)
        detail_response = client.get("/evolution/providers/api-evolution", headers=admin_auth_header)
    finally:
        registry.clear()
        app.config["AGENT_CONFIG"] = old_cfg

    assert response.status_code == 200
    payload = response.json["data"]
    assert payload["providers"][0]["provider_name"] == "api-evolution"
    assert payload["providers"][0]["default"] is True
    assert payload["health"]["status"] == "available"
    assert payload["config"]["analyze_only"] is True
    assert payload["config"]["apply_allowed"] is False
    assert payload["config"]["provider_overrides"]["api-evolution"]["bearer_token"] == "***REDACTED_TOKEN***"
    assert payload["config"]["provider_overrides"]["api-evolution"]["headers"]["Authorization"] == "***REDACTED_CREDENTIAL***"
    matrix = payload["providers"][0]["capability_matrix"]
    assert matrix["analyze"]["available"] is True
    assert matrix["validate"]["supported"] is True
    assert matrix["validate"]["available"] is False
    assert matrix["validate"]["fail_closed_reason"] == "evolution_provider_analyze_only"
    assert matrix["apply"]["fail_closed_reason"] == "evolution_provider_analyze_only"

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
    assert analyze_payload["trace_id"] == "trace-api"
    assert analyze_payload["provider_metadata"]["source"] == "api-test"
    assert analyze_payload["review_status"] == {
        "required": True,
        "status": "review_required",
        "proposal_count": 1,
    }
    assert analyze_payload["proposals"][0]["proposal_id"] == analyze_payload["proposal_ids"][0]
    assert analyze_payload["proposals"][0]["links"]["read_model"] == "/tasks/T-EVO-API/evolution"
    assert analyze_payload["proposals"][0]["links"]["validate"].endswith("/validate")

    assert read_response.status_code == 200
    read_payload = read_response.json["data"]
    assert read_payload["task_id"] == "T-EVO-API"
    assert read_payload["run_count"] == 1
    assert read_payload["proposal_count"] == 1
    assert read_payload["runs"][0]["trigger_type"] == "manual"
    assert read_payload["runs"][0]["provider_metadata"]["source"] == "api-test"
    assert read_payload["proposals"][0]["title"] == "Review failed task"
    assert read_payload["proposals"][0]["provider_metadata"]["evolver_kind"] == "gene"


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
    read_response = client.get("/tasks/T-EVO-VALIDATE/evolution", headers=admin_auth_header)
    proposal = read_response.json["data"]["proposals"][0]
    assert proposal["status"] == "validated"
    assert proposal["validation_summary"]["count"] == 1
    assert proposal["validation_summary"]["last_result"]["status"] == "passed"


def test_task_evolution_apply_endpoint_is_explicitly_policy_gated(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-APPLY", title="Apply proposal", status="failed"))
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(ApiEvolutionEngine(), default=True)
    try:
        analyze_response = client.post(
            "/tasks/T-EVO-APPLY/evolution/analyze",
            headers=admin_auth_header,
            json={"trigger_type": "manual"},
        )
        proposal_id = analyze_response.json["data"]["proposal_ids"][0]
        blocked_response = client.post(
            f"/tasks/T-EVO-APPLY/evolution/proposals/{proposal_id}/apply",
            headers=admin_auth_header,
            json={},
        )

        review_response = client.post(
            f"/tasks/T-EVO-APPLY/evolution/proposals/{proposal_id}/review",
            headers=admin_auth_header,
            json={"action": "approve", "comment": "proposal approved"},
        )

        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["evolution"] = {
            **dict(cfg.get("evolution") or {}),
            "apply_allowed": True,
            "require_review_before_apply": True,
        }
        app.config["AGENT_CONFIG"] = cfg
        apply_response = client.post(
            f"/tasks/T-EVO-APPLY/evolution/proposals/{proposal_id}/apply",
            headers=admin_auth_header,
            json={},
        )
    finally:
        registry.clear()

    assert blocked_response.status_code == 403
    assert blocked_response.json["message"] == "evolution_apply_disabled"
    assert review_response.status_code == 200
    assert review_response.json["data"]["review"]["status"] == "approved"
    assert apply_response.status_code == 200
    assert apply_response.json["data"]["status"] == "prepared"
    assert apply_response.json["data"]["applied"] is False
    read_response = client.get("/tasks/T-EVO-APPLY/evolution", headers=admin_auth_header)
    proposal = read_response.json["data"]["proposals"][0]
    assert proposal["status"] == "apply_prepared"
    assert proposal["apply_summary"]["count"] == 1
    assert proposal["review"]["status"] == "approved"
    assert proposal["apply_summary"]["rollback_hints"]


def test_task_evolution_apply_requires_explicit_review_approval(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-APPLY-REVIEW", title="Apply requires review", status="failed"))
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(ApiEvolutionEngine(), default=True)
    try:
        analyze_response = client.post(
            "/tasks/T-EVO-APPLY-REVIEW/evolution/analyze",
            headers=admin_auth_header,
            json={"trigger_type": "manual"},
        )
        proposal_id = analyze_response.json["data"]["proposal_ids"][0]
        cfg = dict(app.config.get("AGENT_CONFIG") or {})
        cfg["evolution"] = {
            **dict(cfg.get("evolution") or {}),
            "apply_allowed": True,
            "require_review_before_apply": True,
        }
        app.config["AGENT_CONFIG"] = cfg
        response = client.post(
            f"/tasks/T-EVO-APPLY-REVIEW/evolution/proposals/{proposal_id}/apply",
            headers=admin_auth_header,
            json={},
        )
    finally:
        registry.clear()

    assert response.status_code == 403
    assert response.json["message"] == "evolution_apply_requires_approved_review"


def test_task_evolution_review_endpoint_persists_read_model_status(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-REVIEW", title="Review proposal", status="failed"))
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(ApiEvolutionEngine(), default=True)
    try:
        analyze_response = client.post(
            "/tasks/T-EVO-REVIEW/evolution/analyze",
            headers=admin_auth_header,
            json={"trigger_type": "manual"},
        )
        proposal_id = analyze_response.json["data"]["proposal_ids"][0]
        review_response = client.post(
            f"/tasks/T-EVO-REVIEW/evolution/proposals/{proposal_id}/review",
            headers=admin_auth_header,
            json={"action": "approve", "comment": "looks safe"},
        )
        read_response = client.get("/tasks/T-EVO-REVIEW/evolution", headers=admin_auth_header)
    finally:
        registry.clear()

    assert review_response.status_code == 200
    assert review_response.json["data"]["status"] == "approved"
    assert review_response.json["data"]["review"]["status"] == "approved"
    proposal = read_response.json["data"]["proposals"][0]
    assert proposal["review"]["status"] == "approved"
    assert proposal["history"][0]["event_type"] == "proposal_review"


def test_failed_task_completion_auto_triggers_evolution_analysis(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-AUTO", title="Auto trigger evolution", status="assigned"))
    cfg = dict(app.config.get("AGENT_CONFIG") or {})
    cfg["evolution"] = {
        **dict(cfg.get("evolution") or {}),
        "auto_triggers_enabled": True,
        "enabled": True,
    }
    app.config["AGENT_CONFIG"] = cfg
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(ApiEvolutionEngine(), default=True)
    try:
        complete_response = client.post(
            "/tasks/orchestration/complete",
            headers=admin_auth_header,
            json={
                "task_id": "T-EVO-AUTO",
                "trace_id": "trace-auto",
                "actor": "worker-test",
                "output": "[quality_gate] failed: regression",
                "gate_results": {"passed": False, "reason": "regression"},
            },
        )
        read_response = client.get("/tasks/T-EVO-AUTO/evolution", headers=admin_auth_header)
    finally:
        registry.clear()

    assert complete_response.status_code == 200
    trigger = complete_response.json["data"]["evolution_trigger"]
    assert trigger["status"] == "triggered"
    assert trigger["provider_name"] == "api-evolution"

    assert read_response.status_code == 200
    read_payload = read_response.json["data"]
    assert read_payload["run_count"] == 1
    assert read_payload["runs"][0]["trigger_type"] == "verification_failure"
    assert read_payload["proposal_count"] == 1


def test_task_evolution_analyze_rejects_invalid_trigger(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-BAD-TRIGGER", title="Bad trigger"))

    response = client.post(
        "/tasks/T-EVO-BAD-TRIGGER/evolution/analyze",
        headers=admin_auth_header,
        json={"trigger_type": "unknown"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "invalid_trigger_type"


def test_task_evolution_analyze_reports_external_provider_failure_code(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-DOWN", title="External Evolver down", status="failed"))
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(EvolverAdapter(transport=TimeoutEvolverTransport()), default=True)
    try:
        providers_response = client.get("/evolution/providers", headers=admin_auth_header)
        response = client.post(
            "/tasks/T-EVO-DOWN/evolution/analyze",
            headers=admin_auth_header,
            json={"trigger_type": "manual"},
        )
    finally:
        registry.clear()

    assert providers_response.status_code == 200
    health_provider = providers_response.json["data"]["health"]["providers"][0]
    assert health_provider["provider_name"] == "evolver"
    assert health_provider["status"] == "degraded"
    assert health_provider["provider_metadata"]["last_error"]["code"] == "timeout"
    assert response.status_code == 502
    assert response.json["data"]["error_code"] == "provider_timeout"
    assert response.json["data"]["retryable"] is True
    assert response.json["data"]["error_type"] == "EvolverTimeoutError"


def test_task_evolution_validate_and_apply_fail_closed_for_analyze_only_evolver(client, app, admin_auth_header):
    task_repo.save(TaskDB(id="T-EVO-ANALYZE-ONLY", title="Analyze only Evolver", status="failed"))
    old_cfg = dict(app.config.get("AGENT_CONFIG") or {})
    cfg = dict(app.config.get("AGENT_CONFIG") or {})
    cfg["evolution"] = {
        **dict(cfg.get("evolution") or {}),
        "validate_allowed": True,
        "apply_allowed": True,
        "require_review_before_apply": False,
    }
    app.config["AGENT_CONFIG"] = cfg
    registry = get_evolution_provider_registry()
    registry.clear()
    registry.register(EvolverAdapter(transport=AnalyzeOnlyEvolverTransport()), default=True)
    try:
        analyze_response = client.post(
            "/tasks/T-EVO-ANALYZE-ONLY/evolution/analyze",
            headers=admin_auth_header,
            json={"trigger_type": "manual"},
        )
        proposal_id = analyze_response.json["data"]["proposal_ids"][0]
        validate_response = client.post(
            f"/tasks/T-EVO-ANALYZE-ONLY/evolution/proposals/{proposal_id}/validate",
            headers=admin_auth_header,
            json={},
        )
        apply_response = client.post(
            f"/tasks/T-EVO-ANALYZE-ONLY/evolution/proposals/{proposal_id}/apply",
            headers=admin_auth_header,
            json={},
        )
    finally:
        registry.clear()
        app.config["AGENT_CONFIG"] = old_cfg

    assert analyze_response.status_code == 200
    assert validate_response.status_code == 403
    assert validate_response.json["data"]["error_code"] == "evolution_policy_blocked"
    assert validate_response.json["message"] == "evolution_provider_analyze_only"
    assert apply_response.status_code == 403
    assert apply_response.json["data"]["error_code"] == "evolution_policy_blocked"
    assert apply_response.json["message"] == "evolution_provider_analyze_only"
