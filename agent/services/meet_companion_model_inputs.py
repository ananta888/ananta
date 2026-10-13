"""Hub admission of exact tool results as inputs to a separate model run."""

from typing import Protocol

from agent.services.meet_companion_task_tools import digest
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_companion_task_tool import parse, validate_response


class AdmittedToolContentPort(Protocol):
    def read(self, tenant, project, digest): ...


class MeetCompanionModelInputs:
    """Verify origin and current source policy; registered IDs alone are insufficient."""

    def __init__(self, registry, content: AdmittedToolContentPort, policy):
        self.registry, self.content, self.policy = registry, content, policy

    def verify(self, scope, assignment, responses):
        if not scope.controls.chat.enabled or not {"chat.read", "chat.send"} <= set(scope.capabilities):
            raise MeetError("meet_companion_model_chat_disabled", 403)
        if not isinstance(responses, list) or len(responses) > 16:
            raise MeetError("meet_companion_model_tool_inputs_invalid", 403)
        pins, runs = [], set()
        try:
            for response in responses:
                validated = validate_response(response, assignment, response.get("tool"))
                run = self.registry.require_assignment_result(
                    tenant_id=scope.tenant_id, project_id=scope.project_id, projection=validated["run"],
                    task_id=scope.task_id, assignment_id=scope.runtime_id, dispatch_lease_id=scope.lease_id,
                    result_digest=digest(validated),
                )
                if run.run_id in runs or len(run.source_ids) != 1:
                    raise ValueError("tool_input_run_repeated_or_invalid")
                runs.add(run.run_id)
                source = self.registry.require_registered_source(
                    tenant_id=scope.tenant_id, project_id=scope.project_id, source_id=run.source_ids[0],
                )
                intent = parse(self.content.read(scope.tenant_id, scope.project_id, source.content_digest))
                permit = self.policy.authorize(scope, validated["tool"], intent["arguments"])
                if (intent["tool"] != validated["tool"] or digest(intent["assignment"]) != digest(assignment)
                        or intent["chat_revision"] != scope.controls.chat.revision
                        or intent["policy"] != permit.fingerprint
                        or source.origin_type != "meet_companion_task_tool"
                        or source.origin_digest != digest({"kind": "request", **intent})
                        or source.policy_digest != permit.fingerprint or digest(intent) != run.input_digest
                        or run.repository_revision != permit.revision
                        or run.execution_profile_digest != permit.fingerprint
                        or run.environment_digest != permit.environment_digest):
                    raise ValueError("tool_input_policy_or_assignment_changed")
                pin = validated["sources"][0]
                output = self.registry.require_source_identity(
                    tenant_id=scope.tenant_id, project_id=scope.project_id, **{
                        "source_id": pin["source_id"], "expected_binding_digest": pin["binding_digest"],
                    },
                )
                result = {key: validated[key] for key in ("text", "truncated")}
                if (output.origin_type != "meet_companion_task_tool"
                        or output.origin_digest != digest({"kind": "result", "run": run.binding_digest,
                                                          "revision": permit.revision})
                        or output.policy_digest != permit.fingerprint
                        or output.content_digest != digest(result)
                        or output.evidence_scope != run.evidence_scope or output.synthetic != run.synthetic):
                    raise ValueError("tool_output_origin_or_content_changed")
                self.content.read(scope.tenant_id, scope.project_id, output.content_digest)
                pins.append(dict(pin))
        except MeetError:
            raise
        except (ValueError, TypeError, AttributeError, KeyError):
            raise MeetError("meet_companion_model_tool_inputs_unverified", 403) from None
        return pins
