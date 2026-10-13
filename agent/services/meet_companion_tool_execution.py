"""Current read-only operator policy and uniformly sealed CodeCompass dispatch."""

import re
from dataclasses import dataclass

from agent.services.codecompass_retrieval_capability_service import (
    resolve_request_capability,
    verify_retrieval_capability,
)
from agent.services.hub_tool_gateway import GatewayTimeout, run_bounded, timeout_seconds
from agent.services.meet_companion_task_tools import digest
from agent.services.meet_companion_tool_contract import (
    DEFAULT_TIMEOUT_SECONDS,
    _result_payload,
    bounded_text,
    enabled_tools,
    normalize_arguments,
)
from agent.services.meet_contract import MeetError
from agent.services.source_control_access_policy import HubSourcePrincipal

SCOPED_TOOLS = frozenset({"codecompass.retrieve", "codecompass.architecture_overview",
                          "codecompass.architecture_expand", "codecompass.analytics_query", "codecompass.rlm_analyze",
                          "codecompass.architecture_intelligence", "codecompass.layers_heads",
                          "codecompass.layers_plan"})


@dataclass(frozen=True)
class ToolPermit:
    arguments: dict
    capability: dict
    fingerprint: str
    revision: str
    environment_digest: str


class HubCompanionToolPolicy:
    def __init__(self, app, operations, policies, *, observer=None):
        self.app, self.operations, self.policies = app, operations, policies
        self.observer = observer

    def authorize(self, scope, tool, arguments):
        from agent.services.operation_policy_service import OperationAuthContext

        if tool not in enabled_tools():
            raise MeetError("meet_companion_tool_denied", 403)
        descriptor = self.operations.get_for_target(transport="mcp.tool", target=tool)
        policy = self.policies.resolve_policy(self.app.config.get("AGENT_CONFIG", {}) or {})
        decision = self.policies.decide(descriptor, policy, OperationAuthContext(
            auth_source="agent_auth", is_admin=False, approval_granted=False))
        if self.observer is not None:
            self.observer.record(decision, trace_id=None, surface="meet_companion_task_tool",
                                 emit_audit_event=bool(policy.get("emit_audit_events", True)))
        if (not decision.allowed or descriptor is None or descriptor.access_class != "read"
                or descriptor.side_effecting):
            raise MeetError("meet_companion_tool_denied", 403)
        try:
            normalized = normalize_arguments(tool, arguments)
            principal = HubSourcePrincipal(scope.machine_subject, scope.tenant_id,
                                           scope.project_id, frozenset({"agent"}))
            capability = verify_retrieval_capability(resolve_request_capability(
                application=self.app, principal=principal, requested_scope={}))
        except (TypeError, ValueError):
            raise MeetError("meet_companion_tool_scope_denied", 403) from None
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", capability["revision"]):
            raise MeetError("meet_companion_tool_revision_unverified", 403)
        # Resolver TTL timestamps may move between reads. Policy and all actual
        # source authority must stay equal; each read verifies its own expiry.
        source_scope = {key: value for key, value in capability.items()
                        if key not in {"issued_at_epoch", "expires_at_epoch", "capability_digest"}}
        operation = {key: getattr(descriptor, key) for key in (
            "operation_id", "transport", "target", "access_class", "risk_class", "lifecycle", "side_effecting")}
        fingerprint = digest({"tool": tool, "operation": operation,
                              "policy": policy, "scope": source_scope, "arguments": normalized})
        return ToolPermit(normalized, capability, fingerprint, capability["revision"],
                          digest({"adapter": "hub_mcp_registry", "version": 2}))


class HubCompanionToolExecution:
    def __init__(self, app, registry):
        self.app, self.registry = app, registry

    def execute(self, tool, permit, *, current):
        if tool not in SCOPED_TOOLS:
            raise MeetError("meet_companion_tool_scoped_adapter_missing", 403)
        timeout = timeout_seconds("ANANTA_MEET_COMPANION_TOOL_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)

        def work():
            # The shared executor may queue work. Check Task authority when
            # the actual read begins, not merely before submitting it.
            current()
            from agent.services.codecompass_task_capability import task_capability_scope

            with task_capability_scope(dict(permit.capability)):
                return self.registry.call_tool(name=tool, arguments=dict(permit.arguments),
                                               context={"codecompass_capability": dict(permit.capability)})

        try:
            result = run_bounded(self.app, work, timeout)
        except GatewayTimeout:
            raise MeetError("meet_companion_tool_timeout", 504) from None
        except MeetError:
            raise
        except ValueError:
            raise MeetError("meet_companion_tool_arguments_or_scope_invalid", 403) from None
        except KeyError:
            raise MeetError("meet_companion_tool_not_found", 404) from None
        except Exception:  # noqa: BLE001 -- never expose backend exception text to the Worker
            raise MeetError("meet_companion_tool_error", 502) from None
        if isinstance(result, dict) and result.get("isError"):
            raise MeetError("meet_companion_tool_error", 502)
        payload = _result_payload(result)
        if isinstance(payload, dict) and payload.get("status") == "error":
            raise MeetError("meet_companion_tool_error", 502)
        text, truncated = bounded_text(payload)
        return {"text": text, "truncated": truncated}
