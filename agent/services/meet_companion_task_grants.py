"""Assignment-bound grant projection; never create tasks or Worker identities."""

import re
import time
from copy import deepcopy
from typing import Protocol

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import controls_projection

FIELDS = frozenset({"project_id", "task_id", "lease_id", "runtime_id", "session_id", "capabilities"})
IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
CAPABILITIES = frozenset({"avatar.publish", "speech.publish", "chat.send", "chat.read"})


def closed_companion_assignment(request):
    if (not isinstance(request, dict) or set(request) != FIELDS
            or any(not isinstance(request[key], str) or not IDENTITY.fullmatch(request[key])
                   for key in FIELDS - {"capabilities"})):
        raise MeetError("meet_companion_assignment_invalid", 403)
    caps = request["capabilities"]
    if (not isinstance(caps, list) or not caps or len(caps) > len(CAPABILITIES)
            or any(not isinstance(capability, str) or capability not in CAPABILITIES for capability in caps)
            or len(set(caps)) != len(caps)):
        raise MeetError("meet_companion_assignment_invalid", 403)
    return {key: tuple(value) if key == "capabilities" else value for key, value in request.items()}


class TaskAuthorityPort(Protocol):
    def current(self, task_id, lease_id, runtime_id): ...


class GrantIssuerPort(Protocol):
    def issue_dialog(self, authority, task_id, lease_id, runtime_id, now): ...


class ModelProjectionPort(Protocol):
    def projection(self, authority): ...


class BoundCompanionAuthority:
    """Validate caller binding at each signer lookup, including its own reread."""

    def __init__(self, authority, request):
        self.authority, self.request = authority, request

    def current(self, task_id, lease_id, runtime_id):
        value = self.request
        if (task_id, lease_id, runtime_id) != tuple(value[key] for key in ("task_id", "lease_id", "runtime_id")):
            raise MeetError("meet_companion_assignment_mismatch", 403)
        scope = self.authority.current(task_id, lease_id, runtime_id)
        if not getattr(scope, "companion_execution", False):
            raise MeetError("meet_companion_execution_not_bound", 403)
        if (scope.project_id != value["project_id"] or scope.session_id != value["session_id"]
                or set(scope.capabilities) != set(value["capabilities"])):
            raise MeetError("meet_companion_assignment_mismatch", 403)
        return scope


class MeetCompanionTaskGrants:
    """Read existing Hub authority and sign its exact closed assignment scope.

    Task admission and dispatch remain separate Hub services. This facade
    projects only their existing authority; consuming Workers enforce the
    signed control freshness contract before each assigned operation.
    """

    def __init__(self, authority: TaskAuthorityPort, issuer: GrantIssuerPort, *, clock=time.time,
                 model_policy: ModelProjectionPort | None = None):
        self.authority, self.issuer, self.clock = authority, issuer, clock
        self.model_policy = model_policy

    def issue(self, request):
        # Copy caller state before the first port call; neither issuer nor
        # authority lookup may race a mutation of the request/capability list.
        closed = closed_companion_assignment(request)
        bound = BoundCompanionAuthority(self.authority, closed)
        arguments = tuple(closed[key] for key in ("task_id", "lease_id", "runtime_id"))
        before = deepcopy(bound.current(*arguments))
        policy = self.model_policy
        model = policy.projection(before) if policy is not None else None
        grant = self.issuer.issue_dialog(bound, *arguments, self.clock())
        after = bound.current(*arguments)
        if after != before:
            raise MeetError("meet_companion_assignment_changed", 403)
        if self.model_policy is not policy or (policy is not None and policy.projection(after) != model):
            raise MeetError("meet_companion_model_profile_changed", 403)
        return {"schema": "ananta.meet-companion-task-grant.v1", **grant,
                "task_id": after.task_id, "lease_id": after.lease_id, "runtime_id": after.runtime_id,
                "session_id": after.session_id, "project_id": after.project_id,
                "deadline": after.deadline, "capabilities": list(after.capabilities),
                "controls": controls_projection(after.controls),
                **({"model_execution": model} if model is not None else {})}
