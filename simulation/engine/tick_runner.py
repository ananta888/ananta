"""TickRunner — drives one simulation tick (SIM-021)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from simulation.adapters.base import AdapterResponse, SimulationModelAdapter
from simulation.adapters.model_strategy import ModelStrategyResolver
from simulation.engine.budget_guard import BudgetGuard, BudgetViolation
from simulation.engine.economy import ResourceRegenSystem
from simulation.engine.prompt_renderer import PromptRenderer, RenderedPrompt
from simulation.engine.survival import SurvivalSystem
from simulation.models.action import ActionProposal
from simulation.models.memory import MemoryStore
from simulation.models.world_state import SimEvent, WorldState
from simulation.policies.governance import GovernanceSystem
from simulation.policies.policy_engine import PolicyEngine

logger = logging.getLogger(__name__)


@dataclass
class TickResult:
    tick: int
    agent_decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[SimEvent] = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    failures: int = 0
    budget_violation: BudgetViolation | None = None
    state_hash: str = ""


class TickRunner:
    """Executes one full simulation tick.

    Order per tick:
      1. ResourceRegenSystem
      2. For each living agent: render prompt → call adapter → validate → apply
      3. GovernanceSystem.tick()
      4. SurvivalSystem.tick()
      5. MemoryStore.flush_all()
      6. state.advance_tick()
    """

    def __init__(
        self,
        strategy_resolver: ModelStrategyResolver,
        policy_engine: PolicyEngine | None = None,
        survival: SurvivalSystem | None = None,
        governance: GovernanceSystem | None = None,
        memory_store: MemoryStore | None = None,
        prompt_renderer: PromptRenderer | None = None,
        resource_regen: ResourceRegenSystem | None = None,
        profile_loader: Any = None,   # AgentProfileLoader — optional
        on_event: Callable[[SimEvent], None] | None = None,
    ) -> None:
        self.strategy_resolver = strategy_resolver
        self.policy = policy_engine or PolicyEngine()
        self.survival = survival or SurvivalSystem()
        self.governance = governance or GovernanceSystem()
        self.memory_store = memory_store or MemoryStore()
        self.renderer = prompt_renderer or PromptRenderer()
        self.regen = resource_regen or ResourceRegenSystem()
        self._profile_loader = profile_loader
        self._on_event = on_event

    def run_tick(self, state: WorldState, budget_guard: BudgetGuard) -> TickResult:
        result = TickResult(tick=state.tick)

        # 1. Resource regeneration
        self.regen.tick(state)

        # 2. Agent decisions
        for agent in state.living_agents():
            profile = None
            if self._profile_loader and agent.profile_id:
                try:
                    profile = self._profile_loader.load_dict({"id": agent.profile_id,
                                                               "name": agent.name})
                except Exception:
                    pass

            memory = self.memory_store.get(agent.id)
            prompt = self.renderer.render(state, agent, profile, memory)
            adapter = self.strategy_resolver.resolve(agent.id)

            resp = self._call_adapter(adapter, prompt, agent.id)
            result.tokens_used += resp.tokens_used
            result.cost_usd += resp.cost_usd

            if not resp.ok:
                result.failures += 1
                proposal = ActionProposal.invalid_fallback(agent.id, resp.raw_text)
            else:
                proposal = resp.proposal  # type: ignore[assignment]

            validation = self.policy.validate(state, proposal)
            self.policy.apply(state, proposal, validation)

            result.agent_decisions[agent.id] = {
                "proposal": proposal.model_dump(),
                "decision": validation.decision,
                "reason": validation.reason,
            }

            # Perceive own outcome
            memory.perceive(state.tick, "outcome",
                             f"I did {proposal.action_type}: {validation.decision}",
                             importance=0.6)

        # 3. Governance
        gov_events = self.governance.tick(state)
        result.events.extend(gov_events)

        # 4. Survival decay
        death_events = self.survival.tick(state)
        result.events.extend(death_events)
        if death_events:
            result.failures += len(death_events)

        # 5. Memory flush
        self.memory_store.flush_all()

        # 6. Advance tick + hash
        state.advance_tick()
        result.state_hash = state.state_hash()

        # 7. Budget check
        violation = budget_guard.record_tick(
            state,
            tokens_used=result.tokens_used,
            cost_usd=result.cost_usd,
            failures=result.failures,
        )
        result.budget_violation = violation

        # Notify
        for ev in result.events:
            if self._on_event:
                self._on_event(ev)

        return result

    def _call_adapter(self, adapter: SimulationModelAdapter,
                       prompt: RenderedPrompt, agent_id: str) -> AdapterResponse:
        try:
            return adapter.generate(prompt.as_messages(), agent_id=agent_id)
        except Exception as exc:
            logger.warning("adapter error for %s: %s", agent_id, exc)
            from simulation.adapters.base import AdapterResponse
            fallback = ActionProposal.invalid_fallback(agent_id, str(exc))
            return AdapterResponse(raw_text="", proposal=fallback,
                                    parse_error=str(exc), model_id=adapter.model_id)
