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
from .core import AgentRepository, ConfigRepository, ScheduledTaskRepository, TeamRepository, TemplateRepository
from .goals import GoalRepository, PlanNodeRepository, PlanRepository
from .governance import AuditLogRepository, PolicyDecisionRepository, VerificationRecordRepository
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
    "AgentRepository",
    "ArchivedTaskRepository",
    "AuditLogRepository",
    "BannedIPRepository",
    "BlueprintArtifactRepository",
    "BlueprintRoleRepository",
    "ConfigRepository",
    "GoalRepository",
    "LoginAttemptRepository",
    "PasswordHistoryRepository",
    "PlanNodeRepository",
    "PlanRepository",
    "PolicyDecisionRepository",
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
]
