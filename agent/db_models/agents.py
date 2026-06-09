from __future__ import annotations

import time
from typing import List, Optional

from sqlmodel import JSON, Column, Field, SQLModel


class AgentInfoDB(SQLModel, table=True):
    __tablename__ = "agents"
    url: str = Field(primary_key=True)
    name: str
    role: str = "worker"
    token: Optional[str] = None
    worker_roles: List[str] = Field(default=[], sa_column=Column(JSON))
    capabilities: List[str] = Field(default=[], sa_column=Column(JSON))
    runtime_targets: List[dict] = Field(default=[], sa_column=Column(JSON))
    execution_limits: dict = Field(default={}, sa_column=Column(JSON))
    registration_validated: bool = True
    validation_errors: List[str] = Field(default=[], sa_column=Column(JSON))
    validated_at: Optional[float] = None
    last_seen: float = Field(default_factory=time.time)
    status: str = "online"
