from __future__ import annotations

import time
import uuid
from typing import Optional

from sqlmodel import JSON, Column, Field, SQLModel


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
    blueprint_id: Optional[str] = Field(default=None, foreign_key="team_blueprints.id")
    is_active: bool = False
    role_templates: dict = Field(default={}, sa_column=Column(JSON))
    blueprint_snapshot: dict = Field(default={}, sa_column=Column(JSON))


class TeamMemberDB(SQLModel, table=True):
    __tablename__ = "team_members"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    team_id: str = Field(foreign_key="teams.id")
    agent_url: str = Field(foreign_key="agents.url")
    role_id: str = Field(foreign_key="roles.id")
    blueprint_role_id: Optional[str] = Field(default=None, foreign_key="blueprint_roles.id")
    custom_template_id: Optional[str] = Field(default=None, foreign_key="templates.id")


class TeamBlueprintDB(SQLModel, table=True):
    __tablename__ = "team_blueprints"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    base_team_type_name: Optional[str] = None
    is_seed: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
