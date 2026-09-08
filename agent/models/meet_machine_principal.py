"""Hub assignment identity, not release evidence or caller-selected persona metadata."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from agent.models.meet_organization_topology import MeetTopologyScope
from agent.models.meet_role_assignment import MeetRoleFacts, publisher_origin

SCHEMA = "ananta.meet-machine-principal.v1"


def _identifier(value, *, optional=False):
    if optional and value == "":
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,191}", value):
        raise ValueError("meet_machine_principal_invalid")
    return value


@dataclass(frozen=True)
class MeetMachinePrincipal:
    tenant_id: str
    project_id: str
    organization_id: str
    role_slot_id: str
    assignment_id: str
    agent_ref: str

    def __post_init__(self):
        for value in (self.tenant_id, self.project_id, self.organization_id, self.role_slot_id, self.assignment_id):
            _identifier(value)
        if not isinstance(self.agent_ref, str) or not re.fullmatch(r"[a-f0-9]{64}", self.agent_ref):
            raise ValueError("meet_machine_principal_invalid")

    @property
    def subject(self):
        # Version and field names domain-separate this identity from evidence
        # registries, room invites and unrelated content hashes.
        encoded = json.dumps({"schema": SCHEMA, **asdict(self)}, sort_keys=True, separators=(",", ":"))
        return "org-agent-" + hashlib.sha256(encoded.encode()).hexdigest()

    def projection(self):
        return {"schema": SCHEMA, "subject": self.subject, **asdict(self)}


def _from_scope(scope, assignment_id, publisher_url):
    return MeetMachinePrincipal(
        scope.tenant_id,
        scope.project_id,
        scope.organization_id,
        scope.role_slot_id,
        assignment_id,
        hashlib.sha256(publisher_origin(publisher_url).encode()).hexdigest(),
    )


def principal_from_role_facts(scope, facts):
    """Pure derivation after the Hub's role-resolution port checked current rows."""
    if not isinstance(scope, MeetTopologyScope) or not isinstance(facts, MeetRoleFacts):
        raise ValueError("meet_machine_principal_invalid")
    facts.require_eligible()
    return _from_scope(scope, facts.assignment_id, facts.publisher_url)


def principal_from_verified_role(binding):
    """Derive only after the role port has admitted/revalidated this binding."""
    try:
        if (
            not isinstance(binding, dict)
            or set(binding)
            != {
                "schema",
                "task_id",
                "parent_task_id",
                "lease_id",
                "runtime_id",
                "scope",
                "assignment_id",
                "publisher_url",
                "snapshot_digest",
            }
            or binding["schema"] != "ananta.meet-role-binding.v1"
        ):
            raise ValueError()
        for key in ("task_id", "lease_id", "runtime_id", "assignment_id"):
            _identifier(binding[key])
        _identifier(binding["parent_task_id"], optional=True)
        if not isinstance(binding["snapshot_digest"], str) or not re.fullmatch(
            r"[a-f0-9]{64}", binding["snapshot_digest"]
        ):
            raise ValueError()
        scope = binding["scope"]
        if not isinstance(scope, dict) or set(scope) != {
            "tenant_id",
            "project_id",
            "organization_id",
            "unit_id",
            "team_id",
            "role_slot_id",
        }:
            raise ValueError()
        _identifier(scope["unit_id"], optional=True)
        _identifier(scope["team_id"], optional=True)
        return _from_scope(MeetTopologyScope(**scope), binding["assignment_id"], binding["publisher_url"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("meet_machine_principal_invalid") from None


def current_machine_principal(execution, verified_role):
    """Absence is legacy; a present invalid principal never downgrades to legacy."""
    if not isinstance(execution, dict):
        raise ValueError("meet_machine_principal_invalid")
    if "meet_machine_principal" not in execution:
        return None
    expected = principal_from_verified_role(verified_role)
    if execution["meet_machine_principal"] != expected.projection():
        raise ValueError("meet_machine_principal_changed")
    return expected
