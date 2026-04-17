from agent.db_models import EvolutionProposalDB, EvolutionRunDB, TaskDB
from agent.repository import audit_repo, evolution_proposal_repo, evolution_run_repo, task_repo
from agent.services.evolution import (
    EvolutionCapability,
    EvolutionContext,
    EvolutionEngine,
    EvolutionProposal,
    EvolutionResult,
    EvolutionTrigger,
    EvolutionTriggerType,
    PersistedEvolutionAnalysis,
)
from agent.services.evolution.registry import EvolutionProviderRegistry
from agent.services.evolution_service import EvolutionService


class ProposalEngine(EvolutionEngine):
    provider_name = "proposal-engine"
    capabilities = [EvolutionCapability.ANALYZE, EvolutionCapability.PROPOSE]

    def analyze(self, context: EvolutionContext) -> EvolutionResult:
        return EvolutionResult(
            provider_name=self.provider_name,
            status="completed",
            summary=f"Proposal summary for {context.task_id}",
            provider_metadata={"provider_score": 0.91},
            raw_payload={"raw": "retained"},
            proposals=[
                EvolutionProposal(
                    title="Add queue visibility",
                    description="Expose clearer task queue state before retrying.",
                    proposal_type="process_improvement",
                    target_refs=[{"kind": "task", "task_id": context.task_id}],
                    rationale="The task failed verification.",
                    risk_level="medium",
                    confidence=0.8,
                    requires_review=True,
                    provider_metadata={"vendor_hint": "queue"},
                    raw_payload={"vendor_payload": "kept"},
                )
            ],
        )


def test_analyze_task_persists_run_proposals_and_audit_events():
    # Importing the models at module load ensures SQLModel metadata contains the new tables before test DB init.
    assert EvolutionRunDB.__tablename__ == "evolution_runs"
    assert EvolutionProposalDB.__tablename__ == "evolution_proposals"

    task_repo.save(
        TaskDB(
            id="T-EVO-PERSIST",
            title="Investigate failed queue task",
            description="Verification failed after retry.",
            status="failed",
            goal_id="G-EVO",
            goal_trace_id="trace-evo",
            plan_id="P-EVO",
            required_capabilities=["coding"],
            verification_spec={"required": True},
        )
    )
    registry = EvolutionProviderRegistry()
    registry.register(ProposalEngine(), default=True)
    service = EvolutionService(registry=registry)

    persisted = service.analyze_task(
        "T-EVO-PERSIST",
        trigger=EvolutionTrigger(
            trigger_type=EvolutionTriggerType.VERIFICATION_FAILURE,
            source="verification_service",
            actor="verification_service",
            reason="quality_gate_failed",
        ),
    )

    assert isinstance(persisted, PersistedEvolutionAnalysis)
    saved_run = evolution_run_repo.get_by_id(persisted.run_id)
    assert saved_run is not None
    assert saved_run.provider_name == "proposal-engine"
    assert saved_run.task_id == "T-EVO-PERSIST"
    assert saved_run.trace_id == "trace-evo"
    assert saved_run.trigger_type == "verification_failure"
    assert saved_run.trigger_source == "verification_service"
    assert saved_run.result_metadata["proposal_count"] == 1
    assert saved_run.provider_metadata == {"provider_score": 0.91}
    assert saved_run.raw_payload == {"raw": "retained"}

    proposals = evolution_proposal_repo.get_by_run_id(saved_run.id)
    assert len(proposals) == 1
    assert proposals[0].title == "Add queue visibility"
    assert proposals[0].proposal_type == "process_improvement"
    assert proposals[0].risk_level == "medium"
    assert proposals[0].confidence == 0.8
    assert proposals[0].requires_review is True
    assert proposals[0].provider_metadata == {"vendor_hint": "queue"}
    assert proposals[0].raw_payload == {"vendor_payload": "kept"}

    actions = {entry.action: entry for entry in audit_repo.get_all(limit=20)}
    assert "evolution_analysis_requested" in actions
    assert "evolution_analysis_completed" in actions
    assert actions["evolution_analysis_completed"].details["provider_name"] == "proposal-engine"
    assert actions["evolution_analysis_completed"].details["trigger_type"] == "verification_failure"
    assert actions["evolution_analysis_completed"].details["proposal_count"] == 1
