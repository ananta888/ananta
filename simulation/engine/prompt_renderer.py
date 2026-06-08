"""PromptRenderer — assembles per-agent LLM prompts from world state (SIM-013)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from simulation.models.agent_profile import AgentProfile
from simulation.models.memory import AgentMemory
from simulation.models.world_state import AgentState, WorldState


@dataclass
class RenderedPrompt:
    system: str
    user: str
    agent_id: str
    tick: int

    def as_messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": self.system},
                {"role": "user", "content": self.user}]


class PromptRenderer:
    """Assembles prompts from world state + profile + memory.

    Output format: JSON with `action_type`, `target`, `args`, `reason`.
    """

    SYSTEM_TEMPLATE = """\
You are {name} in a social simulation. Your role: {role}.

Personality: {personality}
Goals: {goals}
Values: {values}
Fears: {fears}

Survival drive: {survival_priority:.1f}/1.0  (0=altruist, 1=pure survivalist)
Cooperation tendency: {cooperation_tendency:.1f}/1.0

Respond ONLY with a valid JSON object in this exact schema:
{{
  "action_type": "<one of: {allowed_actions}>",
  "target": "<agent_id or location_id or null>",
  "args": {{...}},
  "reason": "<short explanation>"
}}
No other text. No markdown fences.
"""

    USER_TEMPLATE = """\
=== Tick {tick} ===

YOUR STATUS:
{agent_status}

YOUR LOCATION: {location_name}
Resources here: {location_resources}
Other agents here: {colocated_agents}

RECENT MEMORY:
{short_term_memory}

LONG-TERM CONTEXT:
{long_term_summary}

WORLD LAWS:
{laws}

CURRENT OBSERVATIONS:
{perceptions}

What do you do?"""

    def render(
        self,
        state: WorldState,
        agent: AgentState,
        profile: AgentProfile | None,
        memory: AgentMemory | None,
        allowed_actions: list[str] | None = None,
    ) -> RenderedPrompt:
        allowed = allowed_actions or ["move", "eat", "rest", "attack", "give",
                                       "harvest", "communicate", "vote", "noop"]
        loc = state.locations.get(agent.location_id)
        colocated = [
            a.name for a in state.agents_at(agent.location_id)
            if a.id != agent.id
        ]

        system = self.SYSTEM_TEMPLATE.format(
            name=profile.name if profile else agent.name,
            role=profile.role if profile else agent.role,
            personality=getattr(profile, "personality", "") if profile else "",
            goals=", ".join(profile.goals) if profile else "",
            values=", ".join(profile.values) if profile else "",
            fears=", ".join(profile.fears) if profile else "",
            survival_priority=getattr(profile, "survival_priority", 0.5) if profile else 0.5,
            cooperation_tendency=getattr(profile, "cooperation_tendency", 0.5) if profile else 0.5,
            allowed_actions=", ".join(allowed),
        )

        laws_text = "; ".join(
            f"{l.description} (forbidden: {', '.join(l.forbidden_actions)})"
            for l in state.laws.values() if l.active
        ) or "none"

        status = (f"health={agent.health:.2f} hunger={agent.hunger:.2f} "
                  f"energy={agent.energy:.2f} morale={agent.morale:.2f} "
                  f"reputation={agent.reputation:.2f} "
                  f"inventory={json.dumps(agent.inventory)}")

        user = self.USER_TEMPLATE.format(
            tick=state.tick,
            agent_status=status,
            location_name=loc.name if loc else agent.location_id,
            location_resources=json.dumps(loc.resources if loc else {}),
            colocated_agents=", ".join(colocated) or "nobody",
            short_term_memory=json.dumps(memory.short_term_for_prompt() if memory else [], indent=2),
            long_term_summary=(memory.long_term_summary if memory else "") or "none yet",
            laws=laws_text,
            perceptions=json.dumps(memory.perception_for_prompt() if memory else [], indent=2),
        )

        return RenderedPrompt(system=system, user=user,
                               agent_id=agent.id, tick=state.tick)
