"""PolicyEngine for simulated world rules (SIM-007).

Never calls an LLM. Pure deterministic validation.
"""
from __future__ import annotations

import uuid
from typing import Any

from simulation.models.action import (
    ActionDecision, ActionProposal, ActionValidationResult,
    AttackArgs, check_alive,
)
from simulation.models.world_state import SimEvent, WorldState


class PolicyEngine:
    """Validates ActionProposals against world laws and physical constraints."""

    def validate(self, state: WorldState, proposal: ActionProposal) -> ActionValidationResult:
        agent = state.agents.get(proposal.agent_id)
        if not agent:
            return ActionValidationResult(decision="invalid", reason="agent_not_found")
        if not check_alive(agent):
            return ActionValidationResult(decision="invalid", reason="agent_dead")

        # noop is always allowed
        if proposal.action_type == "noop":
            return ActionValidationResult(decision="noop", reason="explicit_noop")

        # Law check
        crime_result = self._check_laws(state, proposal)
        if crime_result:
            return crime_result

        # Physical/inventory precondition checks
        phys = self._check_physical(state, proposal, agent)
        if phys:
            return phys

        # Default: allowed
        return ActionValidationResult(
            decision="allowed",
            reason="passed_all_checks",
            effects=self._compute_effects(state, proposal, agent),
        )

    def apply(self, state: WorldState, proposal: ActionProposal,
              result: ActionValidationResult) -> None:
        """Apply validated effects to world state and log event."""
        event_kind = "action_executed" if result.decision == "allowed" else "action_denied"
        if result.decision == "crime":
            event_kind = "crime"
            self._apply_crime_consequence(state, proposal, result)
        elif result.decision == "allowed":
            self._apply_effects(state, proposal, result)

        state.apply_event(SimEvent(
            tick=state.tick,
            kind=event_kind,
            actor_id=proposal.agent_id,
            description=f"{proposal.action_type}: {result.reason}",
            data={"proposal": proposal.model_dump(), "decision": result.decision},
        ))

    # ── law checking ──────────────────────────────────────────────────────────

    def _check_laws(self, state: WorldState, proposal: ActionProposal) -> ActionValidationResult | None:
        for law in state.laws.values():
            if not law.active:
                continue
            if proposal.action_type in law.forbidden_actions:
                crime_id = f"crime-{uuid.uuid4().hex[:8]}"
                return ActionValidationResult(
                    decision="crime",
                    reason=f"violates_law:{law.id}",
                    crime_id=crime_id,
                    effects=[{"kind": "crime_logged", "law_id": law.id,
                               "penalty": law.penalty, "severity": law.severity}],
                )
        return None

    # ── physical checks ───────────────────────────────────────────────────────

    def _check_physical(self, state: WorldState, proposal: ActionProposal, agent: Any) -> ActionValidationResult | None:
        at = proposal.action_type
        args = proposal.args

        if at == "move":
            dest = args.get("destination_id")
            if dest and dest not in state.locations:
                return ActionValidationResult(decision="invalid", reason="unknown_destination")

        if at in ("eat", "harvest"):
            resource = args.get("resource", "food")
            amount = float(args.get("amount", 1.0))
            loc = state.locations.get(agent.location_id)
            if not loc or loc.resources.get(resource, 0.0) < amount:
                return ActionValidationResult(decision="denied", reason=f"insufficient_{resource}")

        if at == "give":
            resource = args.get("resource", "")
            amount = float(args.get("amount", 1.0))
            if agent.inventory.get(resource, 0.0) < amount:
                return ActionValidationResult(decision="denied", reason=f"insufficient_{resource}_in_inventory")

        if at == "attack":
            target_id = args.get("target_id") or proposal.target
            if target_id and target_id not in state.agents:
                return ActionValidationResult(decision="invalid", reason="unknown_attack_target")

        return None

    # ── effect computation ────────────────────────────────────────────────────

    def _compute_effects(self, state: WorldState, proposal: ActionProposal, agent: Any) -> list[dict[str, Any]]:
        at = proposal.action_type
        args = proposal.args
        effects: list[dict[str, Any]] = []

        if at == "move":
            effects.append({"kind": "agent_move", "agent_id": agent.id,
                             "to": args.get("destination_id")})

        elif at == "eat":
            resource = args.get("resource", "food")
            amount = float(args.get("amount", 1.0))
            effects += [{"kind": "location_resource_delta", "location_id": agent.location_id,
                          "resource": resource, "delta": -amount},
                         {"kind": "agent_stat_delta", "agent_id": agent.id,
                          "stat": "hunger", "delta": -amount * 0.5},
                         {"kind": "agent_stat_delta", "agent_id": agent.id,
                          "stat": "energy", "delta": amount * 0.3}]

        elif at == "harvest":
            resource = args.get("resource", "food")
            amount = float(args.get("amount", 1.0))
            effects += [{"kind": "location_resource_delta", "location_id": agent.location_id,
                          "resource": resource, "delta": -amount},
                         {"kind": "agent_inventory_delta", "agent_id": agent.id,
                          "resource": resource, "delta": amount}]

        elif at == "give":
            target_id = args.get("target_id") or proposal.target
            resource = args.get("resource", "")
            amount = float(args.get("amount", 1.0))
            effects += [{"kind": "agent_inventory_delta", "agent_id": agent.id,
                          "resource": resource, "delta": -amount},
                         {"kind": "agent_inventory_delta", "agent_id": target_id,
                          "resource": resource, "delta": amount},
                         {"kind": "relationship_delta", "source": agent.id, "target": target_id,
                          "trust": 0.05, "friendship": 0.05}]

        elif at == "attack":
            target_id = args.get("target_id") or proposal.target
            intensity = float(args.get("intensity", 0.3))
            effects += [{"kind": "agent_stat_delta", "agent_id": target_id,
                          "stat": "health", "delta": -intensity},
                         {"kind": "agent_stat_delta", "agent_id": agent.id,
                          "stat": "energy", "delta": -0.1},
                         {"kind": "relationship_delta", "source": target_id, "target": agent.id,
                          "fear": intensity * 0.5, "hostility": 0.3},
                         {"kind": "agent_stat_delta", "agent_id": agent.id,
                          "stat": "reputation", "delta": -0.1}]

        elif at == "rest":
            effects += [{"kind": "agent_stat_delta", "agent_id": agent.id,
                          "stat": "energy", "delta": 0.3},
                         {"kind": "agent_stat_delta", "agent_id": agent.id,
                          "stat": "morale", "delta": 0.1}]

        return effects

    # ── effect application ────────────────────────────────────────────────────

    def _apply_effects(self, state: WorldState, proposal: ActionProposal, result: ActionValidationResult) -> None:
        for effect in result.effects:
            kind = effect.get("kind")
            try:
                if kind == "agent_move":
                    ag = state.agents[effect["agent_id"]]
                    old_loc = state.locations.get(ag.location_id)
                    new_loc = state.locations.get(effect["to"])
                    if old_loc and ag.id in old_loc.occupants:
                        old_loc.occupants.remove(ag.id)
                    if new_loc:
                        new_loc.occupants.append(ag.id)
                    ag.location_id = effect["to"]
                elif kind == "location_resource_delta":
                    state.apply_resource_delta(effect["location_id"], effect["resource"], effect["delta"])
                elif kind == "agent_inventory_delta":
                    state.apply_inventory_delta(effect["agent_id"], effect["resource"], effect["delta"])
                elif kind == "agent_stat_delta":
                    ag = state.agents.get(effect["agent_id"])
                    if ag:
                        stat = effect["stat"]
                        delta = float(effect.get("delta", 0.0))
                        current = getattr(ag, stat, 0.0)
                        setattr(ag, stat, max(0.0, min(1.0, current + delta)))
                elif kind == "relationship_delta":
                    state.relationships.update(
                        effect["source"], effect["target"],
                        **{k: v for k, v in effect.items()
                           if k not in ("kind", "source", "target")},
                    )
            except (KeyError, AttributeError):
                pass

    def _apply_crime_consequence(self, state: WorldState, proposal: ActionProposal, result: ActionValidationResult) -> None:
        for effect in result.effects:
            if effect.get("kind") == "crime_logged":
                penalty = effect.get("penalty", "reputation_loss")
                severity = float(effect.get("severity", 1.0))
                ag = state.agents.get(proposal.agent_id)
                if ag:
                    if penalty == "reputation_loss":
                        ag.reputation = max(0.0, ag.reputation - severity * 0.2)
                    elif penalty in ("imprisonment", "exile"):
                        ag.shelter_status = "imprisoned"
                    elif penalty == "death":
                        ag.alive = False
                        state.apply_event(SimEvent(tick=state.tick, kind="death",
                            actor_id=proposal.agent_id, description="died by law penalty",
                            data={"reason": f"law_penalty:{effect.get('law_id')}"}))
