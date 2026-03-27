"""Backward-compatible repository facade.

Historically the project imported all repositories from this module.
The concrete classes now live in `agent.repositories.*` split by domain.
"""

from agent.repositories import (
    AgentRepository,
    ArchivedTaskRepository,
    AuditLogRepository,
    BannedIPRepository,
    BlueprintArtifactRepository,
    BlueprintRoleRepository,
    ConfigRepository,
    GoalRepository,
    LoginAttemptRepository,
    PasswordHistoryRepository,
    PlanNodeRepository,
    PlanRepository,
    PolicyDecisionRepository,
    RefreshTokenRepository,
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
    UserRepository,
    VerificationRecordRepository,
)

# Singletons für Repositories
user_repo = UserRepository()
refresh_token_repo = RefreshTokenRepository()
agent_repo = AgentRepository()
team_repo = TeamRepository()
template_repo = TemplateRepository()
scheduled_task_repo = ScheduledTaskRepository()
task_repo = TaskRepository()
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
team_type_role_link_repo = TeamTypeRoleLinkRepository()

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
    "agent_repo",
    "archived_task_repo",
    "audit_repo",
    "banned_ip_repo",
    "blueprint_artifact_repo",
    "blueprint_role_repo",
    "config_repo",
    "goal_repo",
    "login_attempt_repo",
    "password_history_repo",
    "plan_node_repo",
    "plan_repo",
    "policy_decision_repo",
    "refresh_token_repo",
    "role_repo",
    "scheduled_task_repo",
    "stats_repo",
    "task_repo",
    "team_blueprint_repo",
    "team_member_repo",
    "team_repo",
    "team_type_repo",
    "team_type_role_link_repo",
    "template_repo",
    "user_repo",
    "verification_record_repo",
]
