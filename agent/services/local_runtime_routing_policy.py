"""Fail-closed routing policy over normalized runtime capability claims."""

from __future__ import annotations

from collections.abc import Iterable

from agent.services.local_runtime_capability_contracts import RuntimeModelSnapshot


class LocalRuntimeRoutingPolicy:
    def select(
        self,
        snapshots: Iterable[RuntimeModelSnapshot],
        *,
        required_capabilities: frozenset[str],
    ) -> tuple[RuntimeModelSnapshot, ...]:
        return tuple(
            sorted(
                (
                    snapshot
                    for snapshot in snapshots
                    if all(snapshot.routable(capability) for capability in required_capabilities)
                ),
                key=lambda item: (item.provider_id, item.model_id),
            )
        )


__all__ = ["LocalRuntimeRoutingPolicy"]
