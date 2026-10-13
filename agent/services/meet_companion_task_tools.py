"""Hub-owned current assignment checks around one read tool and evidence run."""

import hashlib
import json
from copy import deepcopy
from typing import Protocol

from agent.services.meet_companion_task_grants import BoundCompanionAuthority, closed_companion_assignment
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_companion_task_tool import RESPONSE_SCHEMA


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class ToolPolicyPort(Protocol):
    def authorize(self, scope, tool, arguments): ...


class ToolExecutionPort(Protocol):
    def execute(self, tool, permit, *, current): ...


class ToolAttemptAdmissionPort(Protocol):
    def claim(self, assignment_digest, nonce): ...


class ToolSourceAdmissionPort(Protocol):
    def admit(self, tenant, project, content): ...


class MeetCompanionTaskTools:
    def __init__(self, authority, policy: ToolPolicyPort, execution: ToolExecutionPort,
                 sources: ToolSourceAdmissionPort, registry, *, attempts: ToolAttemptAdmissionPort, synthetic=False):
        self.authority, self.policy, self.execution = authority, policy, execution
        self.sources, self.registry, self.synthetic = sources, registry, synthetic
        self.attempts = attempts

    def _source(self, scope, value, origin, permit):
        content = self.sources.admit(scope.tenant_id, scope.project_id, canonical(value))
        return self.registry.register_source(
            tenant_id=scope.tenant_id, project_id=scope.project_id,
            origin_type="meet_companion_task_tool", origin_digest=digest(origin),
            content_digest=content, policy_digest=permit.fingerprint,
            evidence_scope="test" if self.synthetic else "local", synthetic=self.synthetic,
        )

    def call(self, request):
        value = deepcopy(request)
        closed = closed_companion_assignment(value["assignment"])
        bound = BoundCompanionAuthority(self.authority, closed)
        arguments = tuple(closed[key] for key in ("task_id", "lease_id", "runtime_id"))
        before = deepcopy(bound.current(*arguments))
        if not before.controls.chat.enabled or not {"chat.read", "chat.send"} <= set(before.capabilities):
            raise MeetError("meet_companion_tool_chat_disabled", 403)
        permit = deepcopy(self.policy.authorize(before, value["tool"], value["arguments"]))
        assignment_digest = digest({"tenant_id": before.tenant_id, **closed})
        self.attempts.claim(assignment_digest, value["nonce"])
        intent = {"assignment": closed, "tool": value["tool"], "arguments": permit.arguments,
                  "policy": permit.fingerprint, "chat_revision": before.controls.chat.revision}
        source = self._source(before, intent, {"kind": "request", **intent}, permit)
        run = self.registry.reserve_run(
            tenant_id=before.tenant_id, project_id=before.project_id,
            task_id=before.task_id, assignment_id=before.runtime_id, dispatch_lease_id=before.lease_id,
            repository_revision=permit.revision, input_digest=digest(intent),
            execution_profile_digest=permit.fingerprint, environment_digest=permit.environment_digest,
            source_ids=[source.source_id], evidence_scope="test" if self.synthetic else "local",
            synthetic=self.synthetic, idempotency_key=assignment_digest + value["nonce"],
        )
        identity = {"tenant_id": before.tenant_id, "project_id": before.project_id,
                    "run_id": run.run_id, "assignment_id": before.runtime_id,
                    "dispatch_lease_id": before.lease_id}
        try:
            projection = self.registry.assignment_projection(**identity, task_id=before.task_id,
                                                            expected_binding_digest=run.binding_digest)
            self._current(bound, arguments, before, value, permit)
            result = self.execution.execute(value["tool"], deepcopy(permit),
                current=lambda: self._current(bound, arguments, before, value, permit))
            if (not isinstance(result, dict) or set(result) != {"text", "truncated"}
                    or not isinstance(result["text"], str) or len(result["text"]) > 6000
                    or type(result["truncated"]) is not bool):
                raise MeetError("meet_companion_tool_result_invalid", 502)
            self._current(bound, arguments, before, value, permit)
            output = self._source(before, result, {"kind": "result", "run": run.binding_digest,
                                                  "revision": permit.revision}, permit)
            response = {"schema": RESPONSE_SCHEMA, "tool": value["tool"], **result,
                        "sources": [{"source_id": output.source_id, "binding_digest": output.binding_digest}],
                        "run": projection}
            # Admission may involve SQL/Registry I/O. Its successful write is
            # not permission to publish after a concurrent Hub withdrawal.
            self._current(bound, arguments, before, value, permit)
            self.registry.record_result(**identity, terminal_state="succeeded", result_digest=digest(response))
        except Exception:
            self.registry.record_result(**identity, terminal_state="failed", result_digest=digest({"failed": True}))
            raise
        return response

    def _current(self, bound, arguments, before, value, permit):
        after = bound.current(*arguments)
        current = self.policy.authorize(after, value["tool"], value["arguments"])
        if after != before or current.fingerprint != permit.fingerprint:
            raise MeetError("meet_companion_tool_authority_changed", 403)
