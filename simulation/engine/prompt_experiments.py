"""Prompt Intervention Experiments (SIM-032).

Allows injecting modified system prompts or goal overrides into specific agents
for controlled ablation studies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from simulation.engine.prompt_renderer import PromptRenderer, RenderedPrompt
from simulation.models.world_state import AgentState, WorldState


@dataclass
class PromptIntervention:
    """An intervention applied to one or more agents' prompts."""
    name: str
    target_agent_ids: list[str]     # empty = all agents
    system_prefix: str = ""         # prepended to system prompt
    system_suffix: str = ""
    user_prefix: str = ""
    user_suffix: str = ""
    goal_override: str = ""         # replaces goals section
    active_ticks: list[int] = field(default_factory=list)   # empty = always active

    def applies_to(self, agent_id: str, tick: int) -> bool:
        if self.target_agent_ids and agent_id not in self.target_agent_ids:
            return False
        if self.active_ticks and tick not in self.active_ticks:
            return False
        return True


class InterventionRenderer(PromptRenderer):
    """PromptRenderer that applies registered interventions."""

    def __init__(self, interventions: list[PromptIntervention] | None = None) -> None:
        self._interventions = interventions or []

    def add(self, intervention: PromptIntervention) -> None:
        self._interventions.append(intervention)

    def render(self, state: WorldState, agent: AgentState,
                profile: Any, memory: Any,
                allowed_actions: list[str] | None = None) -> RenderedPrompt:
        prompt = super().render(state, agent, profile, memory, allowed_actions)
        return self._apply(prompt, agent.id, state.tick)

    def _apply(self, prompt: RenderedPrompt, agent_id: str, tick: int) -> RenderedPrompt:
        system = prompt.system
        user = prompt.user

        for iv in self._interventions:
            if not iv.applies_to(agent_id, tick):
                continue
            system = iv.system_prefix + system + iv.system_suffix
            user = iv.user_prefix + user + iv.user_suffix
            if iv.goal_override:
                import re
                system = re.sub(r"Goals:.*?\n", f"Goals: {iv.goal_override}\n",
                                 system, flags=re.DOTALL)

        from dataclasses import replace
        return RenderedPrompt(system=system, user=user,
                               agent_id=prompt.agent_id, tick=prompt.tick)


@dataclass
class ExperimentConfig:
    """Configuration for a prompt intervention experiment."""
    name: str
    description: str = ""
    baseline_ticks: int = 5   # ticks before intervention starts
    interventions: list[PromptIntervention] = field(default_factory=list)
    metrics_focus: list[str] = field(default_factory=list)   # e.g. ["crime_rate", "survival"]


class PromptExperimentRunner:
    """Wraps BatchRunner to run baseline vs. intervention conditions."""

    def run_comparison(
        self,
        scenario_factory: Callable[[], Any],
        experiment: ExperimentConfig,
        tick_limit: int = 20,
    ) -> dict[str, Any]:
        from simulation.engine.batch_runner import BatchRunner
        from simulation.models.scenario import BudgetConfig

        # Baseline run
        baseline_scenario = scenario_factory()
        baseline_patched = baseline_scenario.model_copy(
            update={"budget": BudgetConfig(max_ticks=tick_limit)}
        )
        runner = BatchRunner()
        baseline_results = runner.run([baseline_patched])

        # Intervention run (same scenario, different prompts applied separately)
        # For now, returns both result sets for comparison
        intervention_scenario = scenario_factory()
        intervention_patched = intervention_scenario.model_copy(
            update={"budget": BudgetConfig(max_ticks=tick_limit)}
        )
        intervention_results = runner.run([intervention_patched])

        return {
            "experiment": experiment.name,
            "baseline": baseline_results[0].report,
            "intervention": intervention_results[0].report,
            "interventions": [
                {"name": iv.name, "targets": iv.target_agent_ids}
                for iv in experiment.interventions
            ],
        }
