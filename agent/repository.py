"""Backward-compatible repository facade.

Historically the project imported all repositories from this module.
The concrete classes now live in `agent.repositories.*` split by domain.
"""

import copy

from agent.repositories import (
    ActionPackRepository,
    AgentRepository,
    AgentSessionRepository,
    ArchivedTaskRepository,
    ArtifactRepository,
    ArtifactVersionRepository,
    AuditLogRepository,
    BannedIPRepository,
    BlueprintArtifactRepository,
    BlueprintRoleRepository,
    BlueprintWorkflowStepRepository,
    ConfigRepository,
    ContextAccessPolicyRepository,
    ContextBundleRepository,
    EvolutionProposalRepository,
    EvolutionRunRepository,
    ExtractedDocumentRepository,
    GoalRepository,
    InstructionOverlayRepository,
    KnowledgeCollectionRepository,
    KnowledgeIndexRepository,
    KnowledgeIndexRunRepository,
    KnowledgeLinkRepository,
    LoginAttemptRepository,
    MemoryEntryRepository,
    OidcIdentityLinkRepository,
    PasswordHistoryRepository,
    PlanningEvaluationRepository,
    PlanningModelProfileRepository,
    PlanningPatternClusterRepository,
    PlanningPromptVersionRepository,
    PlanningReviewItemRepository,
    PlanningRunRepository,
    PlanningTemplateCandidateRepository,
    PlanNodeRepository,
    PlanRepository,
    PlaybookRepository,
    PolicyDecisionRepository,
    PolicySnapshotRepository,
    RefreshTokenRepository,
    RetrievalRunRepository,
    RoleRepository,
    ScheduledTaskRepository,
    StatsRepository,
    TaskRepository,
    TeamBlueprintRepository,
    TeamMemberRepository,
    TeamRepository,
    TeamTypeRepository,
    TeamTypeRoleLinkRepository,
    TemplateRepository,
    TerminalEventRepository,
    TerminalSessionRepository,
    TextQualityCriteriaSetRepository,
    TextQualityEvaluationRepository,
    ToolCallRepository,
    UserInstructionProfileRepository,
    UserRepository,
    VerificationRecordRepository,
    WorkerJobRepository,
    WorkerResultRepository,
    WorkerSlotLeaseRepository,
)


class _OrganizationWorkflowTaskCompletionPolicy:
    """Composition adapter for the Hub-owned completion policy services."""

    def apply(self, *, authoritative_task, candidate_task, session):
        from agent.services.organization_workflow_completion_policy_service import (
            organization_workflow_completion_policy_service,
        )
        from agent.services.organization_workflow_gate_approval_service import (
            organization_workflow_gate_approval_service,
        )

        organization_workflow_gate_approval_service.issue_for_verified_completion(
            authoritative_task=authoritative_task,
            candidate_task=candidate_task,
            session=session,
        )
        decision = organization_workflow_completion_policy_service.evaluate(
            authoritative_task=authoritative_task,
            candidate_task=candidate_task,
            session=session,
        )
        if (
            authoritative_task is not None
            and decision.reason_code == "organization_workflow_step_binding_immutable"
        ):
            authoritative_context = dict(authoritative_task.worker_execution_context or {})
            candidate_context = dict(candidate_task.worker_execution_context or {})
            candidate_context["organization_workflow_step_binding"] = copy.deepcopy(
                authoritative_context["organization_workflow_step_binding"]
            )
            candidate_task.worker_execution_context = candidate_context
            decision = organization_workflow_completion_policy_service.evaluate(
                authoritative_task=authoritative_task,
                candidate_task=candidate_task,
                session=session,
            )
        if decision.applicable and not decision.allowed:
            organization_workflow_completion_policy_service.pending_status(
                candidate_task=candidate_task,
                decision=decision,
            )
        return candidate_task

# Singletons für Repositories
playbook_repo = PlaybookRepository()
action_pack_repo = ActionPackRepository()
user_repo = UserRepository()
oidc_identity_link_repo = OidcIdentityLinkRepository()
refresh_token_repo = RefreshTokenRepository()
agent_repo = AgentRepository()
artifact_repo = ArtifactRepository()
artifact_version_repo = ArtifactVersionRepository()
extracted_document_repo = ExtractedDocumentRepository()
knowledge_collection_repo = KnowledgeCollectionRepository()
knowledge_index_repo = KnowledgeIndexRepository()
knowledge_index_run_repo = KnowledgeIndexRunRepository()
knowledge_link_repo = KnowledgeLinkRepository()
user_instruction_profile_repo = UserInstructionProfileRepository()
instruction_overlay_repo = InstructionOverlayRepository()
retrieval_run_repo = RetrievalRunRepository()
context_bundle_repo = ContextBundleRepository()
context_access_policy_repo = ContextAccessPolicyRepository()
worker_job_repo = WorkerJobRepository()
worker_result_repo = WorkerResultRepository()
worker_slot_lease_repo = WorkerSlotLeaseRepository()
evolution_run_repo = EvolutionRunRepository()
evolution_proposal_repo = EvolutionProposalRepository()
memory_entry_repo = MemoryEntryRepository()
team_repo = TeamRepository()
template_repo = TemplateRepository()
scheduled_task_repo = ScheduledTaskRepository()
task_repo = TaskRepository(completion_policy=_OrganizationWorkflowTaskCompletionPolicy())
archived_task_repo = ArchivedTaskRepository()
config_repo = ConfigRepository()
goal_repo = GoalRepository()
plan_repo = PlanRepository()
plan_node_repo = PlanNodeRepository()
policy_decision_repo = PolicyDecisionRepository()
verification_record_repo = VerificationRecordRepository()
stats_repo = StatsRepository()
audit_repo = AuditLogRepository()
login_attempt_repo = LoginAttemptRepository()
banned_ip_repo = BannedIPRepository()
password_history_repo = PasswordHistoryRepository()
team_type_repo = TeamTypeRepository()
role_repo = RoleRepository()
team_member_repo = TeamMemberRepository()
team_blueprint_repo = TeamBlueprintRepository()
blueprint_role_repo = BlueprintRoleRepository()
blueprint_artifact_repo = BlueprintArtifactRepository()
blueprint_workflow_step_repo = BlueprintWorkflowStepRepository()
team_type_role_link_repo = TeamTypeRoleLinkRepository()
planning_run_repo = PlanningRunRepository()
planning_prompt_version_repo = PlanningPromptVersionRepository()
planning_model_profile_repo = PlanningModelProfileRepository()
planning_evaluation_repo = PlanningEvaluationRepository()
planning_template_candidate_repo = PlanningTemplateCandidateRepository()
planning_pattern_cluster_repo = PlanningPatternClusterRepository()
planning_review_item_repo = PlanningReviewItemRepository()
terminal_session_repo = TerminalSessionRepository()
terminal_event_repo = TerminalEventRepository()
text_quality_criteria_set_repo = TextQualityCriteriaSetRepository()
text_quality_evaluation_repo = TextQualityEvaluationRepository()
agent_session_repo = AgentSessionRepository()
tool_call_repo = ToolCallRepository()
policy_snapshot_repo = PolicySnapshotRepository()

__all__ = [
    "ActionPackRepository",
    "AgentRepository",
    "ArtifactRepository",
    "ArtifactVersionRepository",
    "ArchivedTaskRepository",
    "AuditLogRepository",
    "BannedIPRepository",
    "BlueprintArtifactRepository",
    "BlueprintRoleRepository",
    "BlueprintWorkflowStepRepository",
    "ConfigRepository",
    "ContextBundleRepository",
    "ContextAccessPolicyRepository",
    "ExtractedDocumentRepository",
    "EvolutionProposalRepository",
    "EvolutionRunRepository",
    "GoalRepository",
    "KnowledgeCollectionRepository",
    "KnowledgeIndexRepository",
    "KnowledgeIndexRunRepository",
    "KnowledgeLinkRepository",
    "InstructionOverlayRepository",
    "LoginAttemptRepository",
    "OidcIdentityLinkRepository",
    "MemoryEntryRepository",
    "PasswordHistoryRepository",
    "PlanNodeRepository",
    "PlanRepository",
    "PolicyDecisionRepository",
    "PlaybookRepository",
    "RefreshTokenRepository",
    "RoleRepository",
    "ScheduledTaskRepository",
    "StatsRepository",
    "TaskRepository",
    "AgentSessionRepository",
    "ToolCallRepository",
    "PolicySnapshotRepository",
    "TeamBlueprintRepository",
    "TeamMemberRepository",
    "TeamRepository",
    "TeamTypeRepository",
    "TeamTypeRoleLinkRepository",
    "TemplateRepository",
    "UserInstructionProfileRepository",
    "UserRepository",
    "VerificationRecordRepository",
    "RetrievalRunRepository",
    "WorkerJobRepository",
    "WorkerResultRepository",
    "WorkerSlotLeaseRepository",
    "PlanningRunRepository",
    "PlanningPromptVersionRepository",
    "PlanningModelProfileRepository",
    "PlanningEvaluationRepository",
    "PlanningTemplateCandidateRepository",
    "PlanningPatternClusterRepository",
    "PlanningReviewItemRepository",
    "TerminalSessionRepository",
    "TerminalEventRepository",
    "action_pack_repo",
    "agent_repo",
    "artifact_repo",
    "artifact_version_repo",
    "archived_task_repo",
    "audit_repo",
    "banned_ip_repo",
    "blueprint_artifact_repo",
    "blueprint_role_repo",
    "config_repo",
    "context_bundle_repo",
    "context_access_policy_repo",
    "extracted_document_repo",
    "evolution_proposal_repo",
    "evolution_run_repo",
    "goal_repo",
    "knowledge_collection_repo",
    "knowledge_index_repo",
    "knowledge_index_run_repo",
    "knowledge_link_repo",
    "instruction_overlay_repo",
    "login_attempt_repo",
    "memory_entry_repo",
    "password_history_repo",
    "plan_node_repo",
    "playbook_repo",
    "plan_repo",
    "policy_decision_repo",
    "refresh_token_repo",
    "role_repo",
    "retrieval_run_repo",
    "scheduled_task_repo",
    "stats_repo",
    "task_repo",
    "agent_session_repo",
    "tool_call_repo",
    "policy_snapshot_repo",
    "team_blueprint_repo",
    "team_member_repo",
    "team_repo",
    "team_type_repo",
    "team_type_role_link_repo",
    "template_repo",
    "user_instruction_profile_repo",
    "user_repo",
    "verification_record_repo",
    "worker_job_repo",
    "worker_result_repo",
    "worker_slot_lease_repo",
    "planning_run_repo",
    "planning_prompt_version_repo",
    "planning_model_profile_repo",
    "planning_evaluation_repo",
    "planning_template_candidate_repo",
    "planning_pattern_cluster_repo",
    "planning_review_item_repo",
    "terminal_session_repo",
    "terminal_event_repo",
    "text_quality_criteria_set_repo",
    "text_quality_evaluation_repo",
]
