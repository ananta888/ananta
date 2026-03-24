from dataclasses import dataclass, field
from typing import List, Optional
import uuid


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class Goal:
    id: str = field(default_factory=lambda: _new_id('goal'))
    summary: str = ''
    source: Optional[str] = None


@dataclass
class Plan:
    id: str = field(default_factory=lambda: _new_id('plan'))
    goal_id: str = ''
    title: str = ''
    rationale: Optional[str] = None


@dataclass
class PlanNode:
    id: str = field(default_factory=lambda: _new_id('node'))
    plan_id: str = ''
    title: str = ''
    depends_on: List[str] = field(default_factory=list)
    rationale: Optional[str] = None


@dataclass
class Worker:
    id: str = field(default_factory=lambda: _new_id('worker'))
    roles: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
