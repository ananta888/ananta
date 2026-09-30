"""Idempotent append/replay port for organization events."""

from __future__ import annotations

from typing import Protocol

from agent.models.organization_event import OrganizationEvent


class OrganizationEventStorePort(Protocol):
    def append_once(self, event: OrganizationEvent) -> tuple[bool, OrganizationEvent]: ...

    def list_for_organization(self, organization_id: str) -> tuple[OrganizationEvent, ...]: ...


__all__ = ["OrganizationEventStorePort"]
