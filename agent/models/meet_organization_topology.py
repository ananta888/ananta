"""Content-free immutable input/result of a scoped Meet topology read."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MeetTopologyScope:
    tenant_id: str
    project_id: str
    organization_id: str
    unit_id: str = ""
    team_id: str = ""
    role_slot_id: str = ""


@dataclass(frozen=True)
class MeetTopologySnapshot:
    unit_lifecycle: str | None = None
    team_lifecycle: str | None = None
    team_unit_id: str | None = None
    team_active: bool | None = None
    role_lifecycle: str | None = None
    role_unit_id: str | None = None
