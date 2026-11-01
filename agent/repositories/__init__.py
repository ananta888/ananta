"""Domain-specific repository modules.

This package splits persistence concerns by domain while keeping the existing
`agent.repository` facade available for backward compatibility.
"""

from .artifacts import (
    ArtifactRepository,
    ArtifactVersionRepository,
    ExtractedDocumentRepository,
    KnowledgeCollectionRepository,
    KnowledgeIndexRepository,
    KnowledgeIndexRunRepository,
    KnowledgeLinkRepository,
)
from .auth import (
    BannedIPRepository,
    LoginAttemptRepository,
    OidcIdentityLinkRepository,
    PasswordHistoryRepository,
    RefreshTokenRepository,
    UserRepository,
)
from .context import ContextBundleRepository, RetrievalRunRepository, WorkerJobRepository, WorkerResultRepository
from .context_access_policy_repo import ContextAccessPolicyRepository
from .core import (
    AgentRepository,
    ConfigRepository,
    PlaybookRepository,
    ScheduledTaskRepository,
    TeamRepository,
    TemplateRepository,
)
from .evolution import EvolutionProposalRepository, EvolutionRunRepository
from .goals import GoalRepository, PlanNodeRepository, PlanRepository
from .governance import ActionPackRepository, AuditLogRepository, PolicyDecisionRepository, VerificationRecordRepository
from .instructions import InstructionOverlayRepository, UserInstructionProfileRepository
from .memory import MemoryEntryRepository
from .ml_intern_training import MlInternTrainingRepository
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
from .planning_learning import (
    PlanningEvaluationRepository,
    PlanningModelProfileRepository,
    PlanningPatternClusterRepository,
    PlanningPromptVersionRepository,
    PlanningReviewItemRepository,
    PlanningRunRepository,
    PlanningTemplateCandidateRepository,
)
from .projects import ProjectRepository
from .source_admission_receipt_repository import (
    SourceAdmissionCounters,
    SourceAdmissionReceiptDraft,
    SourceAdmissionReceiptPersistenceError,
    SourceAdmissionReceiptPort,
    SourceAdmissionReceiptRecord,
    SQLSourceAdmissionReceiptRepository,
)
from .tasks import (
    AgentSessionRepository,
    ArchivedTaskRepository,
    PolicySnapshotRepository,
    TaskRepository,
    ToolCallRepository,
)
from .terminal import TerminalEventRepository, TerminalSessionRepository
from .text_quality import (
    TextQualityCriteriaSetRepository,
    TextQualityEvaluationRepository,
)
from .worker_slot_lease import WorkerSlotLeaseRepository

__all__ = [
    "SQLSourceAdmissionReceiptRepository",
    "SourceAdmissionCounters",
    "SourceAdmissionReceiptDraft",
    "SourceAdmissionReceiptPersistenceError",
    "SourceAdmissionReceiptPort",
    "SourceAdmissionReceiptRecord",
    "ProjectRepository",
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
    "OidcIdentityLinkRepository",
    "MemoryEntryRepository",
    "MlInternTrainingRepository",
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
    "TextQualityCriteriaSetRepository",
    "TextQualityEvaluationRepository",
]
