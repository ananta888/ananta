"""Select a configured destination from exact current Hub role-assignment rows."""

from typing import Protocol

from agent.models.meet_organization_topology import MeetTopologyScope
from agent.models.meet_role_assignment import MeetRoleFacts, publisher_origin
from agent.services.meet_contract import MeetError
from agent.services.meet_role_assignment import RoleAssignmentRows


class DialogPublisherSelection(Protocol):
    def select(self, tenant: str, project: str, fields: dict) -> str: ...


class MeetDialogPublishers:
    def __init__(self, rows: RoleAssignmentRows, origins, default):
        if not isinstance(origins, (list, tuple)) or not 1 <= len(origins) <= 8:
            raise ValueError("meet_dialog_publishers_invalid")
        self.origins = tuple(publisher_origin(origin) for origin in origins)
        if len(set(self.origins)) != len(self.origins) or default not in self.origins:
            raise ValueError("meet_dialog_publishers_invalid")
        self.rows, self.default = rows, default

    def select(self, tenant, project, fields):
        if not fields.get("organization_id"):
            return self.default  # Identical explicit legacy destination, no discovery.
        if not fields.get("role_slot_id"):
            raise MeetError("meet_dialog_principal_assignment_required", 403)
        try:
            scope = MeetTopologyScope(tenant, project, **fields)
            found = []
            for origin in self.origins:
                facts = self.rows.read(scope, origin)
                if facts is None:
                    continue
                if not isinstance(facts, MeetRoleFacts) or facts.publisher_url != origin:
                    raise ValueError()
                facts.require_eligible()
                found.append(origin)
        except Exception:
            # An unavailable/malformed directory must not become a fallback route.
            raise MeetError("meet_dialog_publisher_selection_denied", 403) from None
        if len(found) != 1:
            raise MeetError("meet_dialog_publisher_selection_denied", 403)
        return found[0]
