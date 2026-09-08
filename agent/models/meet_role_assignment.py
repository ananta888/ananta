"""Closed Hub role-binding data; local digests are not release evidence IDs."""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent.models.meet_organization_topology import MeetTopologyScope


def publisher_origin(value):
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() for c in value):
        raise ValueError("meet_publisher_identity_invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or value != f"http://{parsed.netloc}"
    ):
        raise ValueError("meet_publisher_identity_invalid")
    return value


def bounded_json(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > 16384:
        raise ValueError("meet_assignment_metadata_invalid")
    return encoded


def capabilities(value):
    if (
        not isinstance(value, list)
        or len(value) > 128
        or any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", v) for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("meet_assignment_capabilities_invalid")
    return frozenset(value)


@dataclass(frozen=True)
class MeetRoleFacts:
    assignment_id: str
    publisher_url: str
    lifecycle: str
    assigned_at: float
    ended_at: float | None
    registration_validated: bool
    status: str
    role: str
    authorized_capabilities_json: str
    execution_limits_json: str
    slot_policy_json: str
    organization_lock_version: int
    definition_revision: str
    effective_policy_hash: str

    def require_eligible(self):
        if (
            not isinstance(self.assignment_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,191}", self.assignment_id)
            or self.lifecycle != "active"
            or self.ended_at is not None
            or type(self.assigned_at) not in (int, float)
            or not math.isfinite(self.assigned_at)
            or self.assigned_at <= 0
            or self.registration_validated is not True
            or self.status != "online"
            or self.role != "worker"
            or type(self.organization_lock_version) is not int
            or self.organization_lock_version < 1
            or any(
                not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v)
                for v in (self.definition_revision, self.effective_policy_hash)
            )
        ):
            raise ValueError("meet_assignment_ineligible")
        publisher_origin(self.publisher_url)
        caps = capabilities(json.loads(self.authorized_capabilities_json))
        policy, limits = json.loads(self.slot_policy_json), json.loads(self.execution_limits_json)
        if (
            not isinstance(policy, dict)
            or not isinstance(limits, dict)
            or set(policy)
            != {"principal_kinds", "required_capabilities", "forbidden_capabilities", "write_access_required"}
        ):
            raise ValueError("meet_assignment_policy_invalid")
        required = capabilities(policy.get("required_capabilities", [])) | {"meet_dialog_session"}
        forbidden = capabilities(policy.get("forbidden_capabilities", []))
        kinds = capabilities(policy.get("principal_kinds", []))
        write = policy.get("write_access_required", False)
        capacity = limits.get("max_concurrent_tasks", limits.get("max_assignments", 1))
        if (
            "agent" not in kinds
            or not kinds <= {"agent", "human"}
            or type(write) is not bool
            or type(capacity) is not int
            or capacity <= 0
            or not required <= caps
            or forbidden & caps
            or write
            and not ({"write_access", "repository_write"} & caps or limits.get("write_access") is True)
        ):
            raise ValueError("meet_assignment_ineligible")

    def digest(self):
        from dataclasses import asdict

        self.require_eligible()
        return hashlib.sha256(bounded_json(asdict(self)).encode()).hexdigest()


def binding_projection(task_id, parent_id, lease_id, runtime_id, scope: MeetTopologyScope, facts: MeetRoleFacts):
    from dataclasses import asdict

    return {
        "schema": "ananta.meet-role-binding.v1",
        "task_id": task_id,
        "parent_task_id": parent_id,
        "lease_id": lease_id,
        "runtime_id": runtime_id,
        "scope": asdict(scope),
        "assignment_id": facts.assignment_id,
        "publisher_url": facts.publisher_url,
        "snapshot_digest": facts.digest(),
    }
