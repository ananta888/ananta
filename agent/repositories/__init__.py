"""Domain-specific repository modules.

This package splits persistence concerns by domain while keeping the existing
`agent.repository` facade available for backward compatibility.
"""

from .auth import (
    BannedIPRepository,
    LoginAttemptRepository,
    PasswordHistoryRepository,
    RefreshTokenRepository,
    UserRepository,
)
from .artifacts import (
    ArtifactRepository,
    ArtifactVersionRepository,
    ExtractedDocumentRepository,
    KnowledgeCollectionRepository,
    KnowledgeIndexRepository,
    KnowledgeIndexRunRepository,
    KnowledgeLinkRepository,
)
from .context import ContextBundleRepository, RetrievalRunRepository, WorkerJobRepository, WorkerResultRepository
from .context_access_policy_repo import ContextAccessPolicyRepository
from .core import AgentRepository, ConfigRepository, PlaybookRepository, ScheduledTaskRepository, TeamRepository, TemplateRepository
from .evolution import EvolutionProposalRepository, EvolutionRunRepository
from .goals import GoalRepository, PlanNodeRepository, PlanRepository
from .governance import ActionPackRepository, AuditLogRepository, PolicyDecisionRepository, VerificationRecordRepository
from .instructions import InstructionOverlayRepository, UserInstructionProfileRepository
from .memory import MemoryEntryRepository
from .operations import StatsRepository
from .organization import (
    BlueprintArtifactRepository,
    BlueprintRoleRepository,
    BlueprintWorkflowStepRepository,
    RoleRepository,
    TeamBlueprintRepository,
    TeamMemberRepository,
    TeamTypeRepository,
    TeamTypeRoleLinkRepository,
)
from .tasks import AgentSessionRepository, ArchivedTaskRepository, PolicySnapshotRepository, TaskRepository, ToolCallRepository
from .worker_slot_lease import WorkerSlotLeaseRepository
from .terminal import TerminalSessionRepository, TerminalEventRepository
from .planning_learning import (
    PlanningEvaluationRepository,
    PlanningModelProfileRepository,
    PlanningPatternClusterRepository,
    PlanningPromptVersionRepository,
    PlanningReviewItemRepository,
    PlanningRunRepository,
    PlanningTemplateCandidateRepository,
)

__all__ = [
    "ActionPackRepository",
    "AgentRepository",
    "ArtifactRepository",
    "ArtifactVersionRepository",
    "ArchivedTaskRepository",
    "AgentSessionRepository",
    "AuditLogRepository",
    "BannedIPRepository",
    "BlueprintArtifactRepository",
    "BlueprintRoleRepository",
    "BlueprintWorkflowStepRepository",
    "ConfigRepository",
    "ContextBundleRepository",
    "ContextAccessPolicyRepository",
    "GoalRepository",
    "ExtractedDocumentRepository",
    "EvolutionProposalRepository",
    "EvolutionRunRepository",
    "KnowledgeCollectionRepository",
    "KnowledgeIndexRepository",
    "KnowledgeIndexRunRepository",
    "KnowledgeLinkRepository",
    "InstructionOverlayRepository",
    "LoginAttemptRepository",
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
]
