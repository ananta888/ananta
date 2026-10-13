"""Reserve before model execution; accept results only under the exact Hub run."""

import re
from copy import deepcopy

from agent.services.meet_companion_model_policy import CompanionModelPolicyPort
from agent.services.meet_companion_task_grants import BoundCompanionAuthority, closed_companion_assignment
from agent.services.meet_companion_task_tools import canonical, digest
from agent.services.meet_contract import MeetError
from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from ananta_contracts.meet_companion_model_input import output_budget, render_tool_result, validate_model_result
from ananta_contracts.meet_companion_task_tool import parse


def authority_digest(scope):
    # Optional speech/avatar changes do not revoke an otherwise current chat.
    return digest({"tenant": scope.tenant_id, "project": scope.project_id, "task": scope.task_id,
                   "lease": scope.lease_id, "runtime": scope.runtime_id, "session": scope.session_id,
                   "deadline": scope.deadline, "subject": scope.machine_subject,
                   "capabilities": sorted(scope.capabilities), "chat": vars(scope.controls.chat)})


class MeetCompanionModelRuns:
    def __init__(self, authority, policy: CompanionModelPolicyPort, inputs, sources, registry, *, attempts,
                 synthetic=False):
        self.authority, self.policy, self.inputs = authority, policy, inputs
        self.sources, self.registry, self.attempts, self.synthetic = sources, registry, attempts, synthetic

    def _bound(self, assignment):
        closed = closed_companion_assignment(assignment)
        bound = BoundCompanionAuthority(self.authority, closed)
        args = tuple(closed[key] for key in ("task_id", "lease_id", "runtime_id"))
        scope = bound.current(*args)
        if not scope.controls.chat.enabled or not {"chat.read", "chat.send"} <= set(scope.capabilities):
            raise MeetError("meet_companion_model_chat_disabled", 403)
        return closed, bound, args, scope

    def _source(self, scope, value, origin, fingerprint):
        content = self.sources.admit(scope.tenant_id, scope.project_id, canonical(value))
        return self.registry.register_source(
            tenant_id=scope.tenant_id, project_id=scope.project_id, origin_type="meet_companion_model",
            origin_digest=digest(origin), content_digest=content, policy_digest=fingerprint,
            evidence_scope="test" if self.synthetic else "local", synthetic=self.synthetic,
        )

    def _current(self, bound, args, intent):
        scope = bound.current(*args)
        permit = self.policy.authorize(scope, intent["backend"], intent["model"])
        if (authority_digest(scope) != intent["authority"] or permit.fingerprint != intent["policy"]
                or permit.revision != intent["revision"] or permit.environment_digest != intent["environment"]):
            raise MeetError("meet_companion_model_authority_changed", 403)
        pins = self.inputs.verify(scope, intent["assignment"], intent["tool_results"])
        if pins != intent["pins"]:
            raise MeetError("meet_companion_model_inputs_changed", 403)
        return scope

    @staticmethod
    def _request(value):
        request = value["request"]
        if (not isinstance(request, dict) or request.get("model") != value["model"]
                or request.get("stream") is not False or not isinstance(request.get("messages"), list)
                or not 1 <= len(request["messages"]) <= 96):
            raise MeetError("meet_companion_model_request_invalid", 403)
        try:
            output_budget(value["backend"], request)
        except (ValueError, AttributeError):
            raise MeetError("meet_companion_model_request_invalid", 403) from None
        messages = request["messages"]
        if any(not isinstance(row, dict) for row in messages):
            raise MeetError("meet_companion_model_request_invalid", 403)
        actual = [row.get("content") for row in messages if row.get("role") == "tool"]
        expected = [render_tool_result(response) for response in value["tool_results"]]
        if (any(not isinstance(text, str) for text in actual)
                or set(actual) != set(expected)):
            raise MeetError("meet_companion_model_tool_messages_unverified", 403)
        return deepcopy(request)

    def reserve(self, assignment, value, *, nonce):
        if (not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce)
                or not isinstance(value, dict) or set(value) != {"backend", "model", "request", "tool_results"}):
            raise MeetError("meet_companion_model_request_invalid", 403)
        value = deepcopy(value)
        closed, bound, args, before = self._bound(assignment)
        permit = self.policy.authorize(before, value["backend"], value["model"])
        pins = self.inputs.verify(before, closed, value["tool_results"])
        request = self._request(value)
        intent = {"schema": "ananta.meet-companion-model-intent.v1", "assignment": closed,
                  "authority": authority_digest(before), "backend": value["backend"], "model": value["model"],
                  "request": request, "tool_results": value["tool_results"], "pins": pins,
                  "policy": permit.fingerprint, "revision": permit.revision, "environment": permit.environment_digest}
        attempt = digest({"domain": "model", "tenant": before.tenant_id, "assignment": closed})
        self.attempts.claim(attempt, nonce)
        source = self._source(before, intent, {"kind": "request", "assignment": closed}, permit.fingerprint)
        run = self.registry.reserve_run(
            tenant_id=before.tenant_id, project_id=before.project_id, task_id=before.task_id,
            assignment_id=before.runtime_id, dispatch_lease_id=before.lease_id, repository_revision=permit.revision,
            input_digest=digest(intent), execution_profile_digest=permit.fingerprint,
            environment_digest=permit.environment_digest,
            source_ids=[source.source_id, *(pin["source_id"] for pin in pins)],
            evidence_scope="test" if self.synthetic else "local", synthetic=self.synthetic,
            idempotency_key=attempt + nonce,
        )
        identity = self._identity(before, run.run_id)
        try:
            self._current(bound, args, intent)
            projection = self.registry.assignment_projection(**identity, task_id=before.task_id,
                                                            expected_binding_digest=run.binding_digest)
        except Exception:
            self.registry.record_result(**identity, terminal_state="failed", result_digest=digest({"failed": True}))
            raise
        return {"schema": "ananta.meet-companion-model-reserved.v1", "run": projection,
                "input_source": {"source_id": source.source_id, "binding_digest": source.binding_digest}}

    @staticmethod
    def _identity(scope, run_id):
        return {"tenant_id": scope.tenant_id, "project_id": scope.project_id, "run_id": run_id,
                "assignment_id": scope.runtime_id, "dispatch_lease_id": scope.lease_id}

    def _projection(self, scope, projection, *, input_digest=None):
        try:
            value = validate_hub_evidence_assignment(projection)
            return self.registry.assignment_projection(**self._identity(scope, value["run_id"]),
                task_id=scope.task_id, expected_binding_digest=value["binding_digest"], input_digest=input_digest)
        except ValueError:
            raise MeetError("meet_companion_model_run_unavailable", 403) from None

    def finish(self, assignment, value):
        if not isinstance(value, dict) or set(value) != {"run", "input_source", "result"}:
            raise MeetError("meet_companion_model_result_invalid", 403)
        value = deepcopy(value)
        closed, bound, args, before = self._bound(assignment)
        projection, pin = value["run"], value["input_source"]
        registered = self._projection(before, projection)
        identity = self._identity(before, projection["run_id"])
        if (not isinstance(pin, dict) or set(pin) != {"source_id", "binding_digest"}
                or registered != projection or pin["source_id"] not in registered["source_ids"]):
            raise MeetError("meet_companion_model_run_mismatch", 403)
        source = self.registry.require_source_identity(tenant_id=before.tenant_id, project_id=before.project_id,
            source_id=pin["source_id"], expected_binding_digest=pin["binding_digest"])
        intent = parse(self.sources.read(before.tenant_id, before.project_id, source.content_digest), 128_000)
        if (intent.get("schema") != "ananta.meet-companion-model-intent.v1"
                or digest(intent["assignment"]) != digest(closed) or source.origin_type != "meet_companion_model"
                or source.origin_digest != digest({"kind": "request", "assignment": closed})
                or source.policy_digest != intent["policy"]
                or sorted(registered["source_ids"]) != sorted([source.source_id,
                                                              *(item["source_id"] for item in intent["pins"])])):
            raise MeetError("meet_companion_model_input_source_invalid", 403)
        self._projection(before, projection, input_digest=digest(intent))
        try:
            current = self._current(bound, args, intent)
            result = value["result"]
            try:
                if not isinstance(result, dict) or len(canonical(result)) > 32_000:
                    raise ValueError()
                validate_model_result(intent["backend"], intent["request"], result)
            except (ValueError, AttributeError, TypeError):
                raise MeetError("meet_companion_model_result_invalid", 403) from None
            output = self._source(current, result, {"kind": "result", "run": projection["binding_digest"]},
                                  intent["policy"])
            self._current(bound, args, intent)
            self.registry.record_result(**identity, terminal_state="succeeded", result_digest=digest(result))
        except Exception:
            self.registry.record_result(**identity, terminal_state="failed", result_digest=digest({"failed": True}))
            raise
        return {"schema": "ananta.meet-companion-model-completed.v1", "run": registered,
                "result_digest": digest(result),
                "sources": [{"source_id": output.source_id, "binding_digest": output.binding_digest}]}
