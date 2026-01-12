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

class AgentInfoDB(SQLModel, table=True):
    __tablename__ = "agents"
    url: str = Field(primary_key=True)
    name: str
    role: str = "worker"
    token: Optional[str] = None
    last_seen: float = Field(default_factory=time.time)
    status: str = "online"

class TeamDB(SQLModel, table=True):
    __tablename__ = "teams"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    description: Optional[str] = None
    type: str = "Scrum"
    agent_names: List[str] = Field(default=[], sa_column=Column(JSON))
    is_active: bool = False

class TemplateDB(SQLModel, table=True):
    __tablename__ = "templates"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str
    description: Optional[str] = None
    prompt_template: str

class ScheduledTaskDB(SQLModel, table=True):
    __tablename__ = "tasks"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    command: str
    interval_seconds: int
    next_run: float
    last_run: Optional[float] = None
    enabled: bool = True

class ConfigDB(SQLModel, table=True):
    __tablename__ = "config"
    key: str = Field(primary_key=True)
    value_json: str # Wir speichern den Wert als JSON-String
