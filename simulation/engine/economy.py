"""Economy/Resource system (SIM-012)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simulation.models.world_state import SimEvent, WorldState


@dataclass
class TradeOffer:
    offer_id: str
    seller_id: str
    give_resource: str
    give_amount: float
    want_resource: str
    want_amount: float
    tick_created: int
    ttl: int = 5
    active: bool = True

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class MarketSystem:
    """Simple barter market; no price discovery — agents propose exact trades."""

    def __init__(self) -> None:
        self._offers: dict[str, TradeOffer] = {}

    def post_offer(self, state: WorldState, seller_id: str,
                    give_resource: str, give_amount: float,
                    want_resource: str, want_amount: float,
                    offer_id: str | None = None) -> TradeOffer:
        import uuid
        oid = offer_id or f"offer-{uuid.uuid4().hex[:8]}"
        offer = TradeOffer(
            offer_id=oid, seller_id=seller_id,
            give_resource=give_resource, give_amount=give_amount,
            want_resource=want_resource, want_amount=want_amount,
            tick_created=state.tick,
        )
        self._offers[oid] = offer
        return offer

    def accept_trade(self, state: WorldState, buyer_id: str, offer_id: str) -> SimEvent | None:
        offer = self._offers.get(offer_id)
        if not offer or not offer.active:
            return None
        seller = state.agents.get(offer.seller_id)
        buyer = state.agents.get(buyer_id)
        if not seller or not buyer:
            return None

        # Validate both parties have the resources
        if seller.inventory.get(offer.give_resource, 0.0) < offer.give_amount:
            return None
        if buyer.inventory.get(offer.want_resource, 0.0) < offer.want_amount:
            return None

        # Execute exchange
        state.apply_inventory_delta(offer.seller_id, offer.give_resource, -offer.give_amount)
        state.apply_inventory_delta(buyer_id, offer.give_resource, offer.give_amount)
        state.apply_inventory_delta(buyer_id, offer.want_resource, -offer.want_amount)
        state.apply_inventory_delta(offer.seller_id, offer.want_resource, offer.want_amount)

        offer.active = False

        # Relationship bump
        state.relationships.update(buyer_id, offer.seller_id, trust=0.05)
        state.relationships.update(offer.seller_id, buyer_id, trust=0.05)

        ev = SimEvent(
            tick=state.tick, kind="action_executed", actor_id=buyer_id,
            description=f"trade: {buyer_id} bought {offer.give_amount}x{offer.give_resource}"
                        f" from {offer.seller_id}",
            data={"kind": "trade", "offer": offer.as_dict()},
        )
        state.apply_event(ev)
        return ev

    def tick(self, state: WorldState) -> None:
        """Expire stale offers."""
        for offer in list(self._offers.values()):
            if offer.active and (state.tick - offer.tick_created) >= offer.ttl:
                offer.active = False

    def active_offers(self) -> list[TradeOffer]:
        return [o for o in self._offers.values() if o.active]


class ResourceRegenSystem:
    """Location resource regeneration and world-level resource accounting."""

    def tick(self, state: WorldState) -> None:
        for loc in state.locations.values():
            for resource, regen_rate in loc.resource_regen.items():
                if regen_rate > 0:
                    current = loc.resources.get(resource, 0.0)
                    loc.resources[resource] = current + regen_rate
