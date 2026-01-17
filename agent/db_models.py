from typing import Optional, List
from sqlmodel import SQLModel, Field, JSON, Column
import time
import uuid

class UserDB(SQLModel, table=True):
    __tablename__ = "users"
    username: str = Field(primary_key=True)
    password_hash: str
    role: str = "user"
    mfa_secret: Optional[str] = None
    mfa_enabled: bool = False
    failed_login_attempts: int = Field(default=0)
    lockout_until: Optional[float] = Field(default=None)

class LoginAttemptDB(SQLModel, table=True):
    __tablename__ = "login_attempts"
    id: Optional[int] = Field(default=None, primary_key=True)
    ip: str = Field(index=True)
    timestamp: float = Field(default_factory=time.time)

class BannedIPDB(SQLModel, table=True):
    __tablename__ = "banned_ips"
    ip: str = Field(primary_key=True)
    banned_until: float
    reason: Optional[str] = None

class AgentInfoDB(SQLModel, table=True):
    __tablename__ = "agents"
    url: str = Field(primary_key=True)
    name: str
    role: str = "worker"
    token: Optional[str] = None
    last_seen: float = Field(default_factory=time.time)
    status: str = "online"

class TeamTypeDB(SQLModel, table=True):
    __tablename__ = "team_types"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None

class RoleDB(SQLModel, table=True):
    __tablename__ = "roles"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = None
    default_template_id: Optional[str] = Field(default=None, foreign_key="templates.id")

class TeamTypeRoleLink(SQLModel, table=True):
    __tablename__ = "team_type_role_links"
    team_type_id: str = Field(foreign_key="team_types.id", primary_key=True)
    role_id: str = Field(foreign_key="roles.id", primary_key=True)
    template_id: Optional[str] = Field(default=None, foreign_key="templates.id")

class TeamDB(SQLModel, table=True):
    __tablename__ = "teams"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    description: Optional[str] = None
    team_type_id: Optional[str] = Field(default=None, foreign_key="team_types.id")
    is_active: bool = False

class TeamMemberDB(SQLModel, table=True):
    __tablename__ = "team_members"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    team_id: str = Field(foreign_key="teams.id")
    agent_url: str = Field(foreign_key="agents.url")
    role_id: str = Field(foreign_key="roles.id")
    custom_template_id: Optional[str] = Field(default=None, foreign_key="templates.id")

class TemplateDB(SQLModel, table=True):
    __tablename__ = "templates"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    description: Optional[str] = None
    prompt_template: str

class ScheduledTaskDB(SQLModel, table=True):
    __tablename__ = "scheduled_tasks"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    command: str
    interval_seconds: int
    next_run: float
    last_run: Optional[float] = None
    enabled: bool = True

class TaskDB(SQLModel, table=True):
    __tablename__ = "tasks"
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    description: Optional[str] = None
    status: str = "todo"
    priority: str = "Medium"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    team_id: Optional[str] = Field(default=None, foreign_key="teams.id")
    assigned_agent_url: Optional[str] = Field(default=None, foreign_key="agents.url")
    assigned_role_id: Optional[str] = Field(default=None, foreign_key="roles.id")
    history: List[dict] = Field(default=[], sa_column=Column(JSON))
    last_proposal: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    last_output: Optional[str] = None
    last_exit_code: Optional[int] = None
    callback_url: Optional[str] = None
    callback_token: Optional[str] = None
    parent_task_id: Optional[str] = None

class ConfigDB(SQLModel, table=True):
    __tablename__ = "config"
    key: str = Field(primary_key=True)
    value_json: str # Wir speichern den Wert als JSON-String

class RefreshTokenDB(SQLModel, table=True):
    __tablename__ = "refresh_tokens"
    token: str = Field(primary_key=True)
    username: str = Field(foreign_key="users.username")
    expires_at: float

class PasswordHistoryDB(SQLModel, table=True):
    __tablename__ = "password_history"
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(foreign_key="users.username", index=True)
    password_hash: str
    created_at: float = Field(default_factory=time.time)

class StatsSnapshotDB(SQLModel, table=True):
    __tablename__ = "stats_history"
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: float = Field(default_factory=time.time, index=True)
    agents: dict = Field(default={}, sa_column=Column(JSON))
    tasks: dict = Field(default={}, sa_column=Column(JSON))
    shell_pool: dict = Field(default={}, sa_column=Column(JSON))
    resources: dict = Field(default={}, sa_column=Column(JSON))

class AuditLogDB(SQLModel, table=True):
    __tablename__ = "audit_logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: float = Field(default_factory=time.time, index=True)
    username: str
    ip: str
    action: str
    details: dict = Field(default={}, sa_column=Column(JSON))
