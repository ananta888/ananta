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
from .core import AgentRepository, ConfigRepository, PlaybookRepository, ScheduledTaskRepository, TeamRepository, TemplateRepository
from .evolution import EvolutionProposalRepository, EvolutionRunRepository
from .goals import GoalRepository, PlanNodeRepository, PlanRepository
from .governance import ActionPackRepository, AuditLogRepository, PolicyDecisionRepository, VerificationRecordRepository
from .memory import MemoryEntryRepository
from .operations import StatsRepository
from .organization import (
    BlueprintArtifactRepository,
    BlueprintRoleRepository,
    RoleRepository,
    TeamBlueprintRepository,
    TeamMemberRepository,
    TeamTypeRepository,
    TeamTypeRoleLinkRepository,
)
from .tasks import ArchivedTaskRepository, TaskRepository

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
    "ConfigRepository",
    "ContextBundleRepository",
    "GoalRepository",
    "ExtractedDocumentRepository",
    "EvolutionProposalRepository",
    "EvolutionRunRepository",
    "KnowledgeCollectionRepository",
    "KnowledgeIndexRepository",
    "KnowledgeIndexRunRepository",
    "KnowledgeLinkRepository",
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
    "TeamBlueprintRepository",
    "TeamMemberRepository",
    "TeamRepository",
    "TeamTypeRepository",
    "TeamTypeRoleLinkRepository",
    "TemplateRepository",
    "UserRepository",
    "VerificationRecordRepository",
    "RetrievalRunRepository",
    "WorkerJobRepository",
    "WorkerResultRepository",
]
