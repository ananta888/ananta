"""ScenarioConfig schema (SIM-002)."""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


TimeMode = Literal["discrete", "continuous"]
RunnerKind = Literal["ananta", "langgraph", "n8n", "dummy"]


class BudgetConfig(BaseModel):
    max_ticks: int = 100
    max_wall_seconds: float = 300.0
    max_tokens: int = 500_000
    max_cost_usd: float = 5.0
    max_consecutive_failures: int = 10
    stop_on_extinction: bool = True


class ModelStrategyEntry(BaseModel):
    agent_id: Optional[str] = None       # None = default for all
    provider: str = "dummy"
    model: str = "dummy-v1"
    temperature: float = 0.7
    json_only: bool = True


class ResourceDefinition(BaseModel):
    name: str
    initial_amount: float = 10.0
    regeneration_per_tick: float = 0.0
    max_amount: Optional[float] = None
    location_id: Optional[str] = None


class LawDefinition(BaseModel):
    id: str
    description: str
    forbidden_actions: list[str] = Field(default_factory=list)
    penalty: str = "reputation_loss"    # reputation_loss | exile | imprisonment | fine | death
    severity: float = 1.0               # 0-1


class ScenarioConfig(BaseModel):
    name: str
    description: str = ""
    seed: int = 42
    tick_limit: int = 50
    time_mode: TimeMode = "discrete"
    runner: RunnerKind = "ananta"
    human_in_loop: bool = False

    agents: list[dict[str, Any]] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[ResourceDefinition] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    laws: list[LawDefinition] = Field(default_factory=list)
    policy_sets: list[str] = Field(default_factory=list)

    model_strategy: list[ModelStrategyEntry] = Field(default_factory=list)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    metadata: dict[str, Any] = Field(default_factory=dict)
