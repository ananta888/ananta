"""Hub consumer of the VisionDecision specialist: task policy, actions, confirmations, escalations.

``run_vision_decision`` is what ``POST /v1/vision/decision`` does after auth and exposure policy:

1. the provider scores the task's schema on the caller's images (``ok=False`` -> ``normal_path``);
2. ``gate_vision_decision`` maps every field; :class:`VisionTaskPolicy` is the only thing that can
   yield ``act`` (a policy error denies);
3. ``act``: an informational value is released, an action runs through the executor after the exposure
   policy is checked again; ``confirm``: an action gets a short-lived, single-use confirmation bound to
   tenant, subject and the concrete action type (the voice confirmation store and executor, own
   instances), an informational value is returned marked ``confirm``; ``deny``: no value;
4. ``escalate``: :class:`VisionEscalationExecutor` asks a chat completion/larger model (unscored
   suggestion -> at most ``confirm``) or issues a single-use human review ticket.

``grants_permission`` is ``False`` everywhere. Audit projections carry field names, actions, rules,
scores, error codes, usage and timings only: no image, prompt, value, action type or id.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from agent.services.audio_decision_command_executor import (
    ConfirmationError,
    Handler,
    VoiceCommandConfirmationStore,
    VoiceCommandExecution,
    VoiceCommandExecutor,
    VoiceCommandInvocation,
    confirm_voice_command,
    confirmation_ttl_seconds,
)
from agent.services.platform_governance_service import get_platform_governance_service
from agent.services.vision_decision_escalation_executor import (
    VisionEscalationConfig,
    VisionEscalationExecutor,
    build_escalation_executor,
    escalation_action_type,
)
from agent.services.vision_decision_hub_gate import (
    VisionDecisionKind,
    VisionFieldDecision,
    VisionHubAction,
    VisionPolicyVerdict,
    VisionProposal,
    gate_vision_decision,
)
from agent.services.vision_decision_provider import (
    VisionContext,
    VisionDecisionConfig,
    VisionDecisionProvider,
    VisionImage,
    get_vision_decision_provider,
)
from agent.services.vision_decision_task_policy import VisionTask, VisionTaskAction, VisionTaskPolicy, VisionTaskPolicyDecision
from agent.services.voice_governance_domain import VoicePrincipal

DECISION_ROUTE = "/v1/vision/decision"
CONFIRM_ROUTE = "/v1/vision/decision/confirm"
# Rejected by the provider before anything left the process: a caller error, not a service outcome.
LOCAL_REJECTION_CODES = frozenset({"invalid_image", "image_too_large", "too_many_images", "image_decoder_unavailable"})
_DATA_URI = re.compile(r"^data:image/(png|jpeg|jpg|webp);base64,", re.IGNORECASE)
_ESCALATION_ENV_KEYS = (
    "VISION_DECISION_ESCALATION_CHAT",
    "VISION_DECISION_ESCALATION_CHAT_URL",
    "VISION_DECISION_ESCALATION_CHAT_MODEL",
    "VISION_DECISION_ESCALATION_LARGER_URL",
    "VISION_DECISION_ESCALATION_LARGER_MODEL",
    "VISION_DECISION_ESCALATION_TTL_SECONDS",
)

Authorize = Callable[[], bool]


class VisionRequestError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


# ---------------------------------------------------------------------------------------------------------
# Exposure policy (``exposure_policy.vision_decision``; kept out of the shared normalized snapshot)


_EXPOSURE_DEFAULTS = {
    "enabled": True,
    "allow_agent_auth": False,
    "allow_user_auth": True,
    "require_admin_for_user_auth": False,
    "allow_human_resolution": True,
    "emit_audit_events": True,
}
EXPOSURE_OPERATIONS = frozenset({"decide", "confirm", "resolve"})


@dataclass(frozen=True)
class VisionAccessDecision:
    allowed: bool
    reason: str
    auth_source: str
    policy: dict[str, Any]


def resolve_vision_exposure_policy(cfg: dict[str, Any] | None) -> dict[str, Any]:
    exposure = get_platform_governance_service().resolve_exposure_policy(cfg)
    raw = exposure.get("vision_decision") if isinstance(exposure, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    return {key: raw[key] if isinstance(raw.get(key), bool) else default for key, default in _EXPOSURE_DEFAULTS.items()}


def evaluate_vision_access(
    *, cfg: dict[str, Any] | None, is_agent_auth: bool, is_user_auth: bool, is_admin: bool, operation: str
) -> VisionAccessDecision:
    policy = resolve_vision_exposure_policy(cfg)
    auth_source = "agent_auth" if is_agent_auth else "user_jwt" if is_user_auth else "unknown"
    if operation not in EXPOSURE_OPERATIONS:
        return VisionAccessDecision(False, "vision_operation_unknown", auth_source, policy)
    if not policy["enabled"]:
        return VisionAccessDecision(False, "vision_exposure_disabled", auth_source, policy)
    if is_agent_auth and not policy["allow_agent_auth"]:
        return VisionAccessDecision(False, "vision_agent_auth_disabled", auth_source, policy)
    if is_user_auth:
        if not policy["allow_user_auth"]:
            return VisionAccessDecision(False, "vision_user_auth_disabled", auth_source, policy)
        if policy["require_admin_for_user_auth"] and not is_admin:
            return VisionAccessDecision(False, "vision_admin_required", auth_source, policy)
    if operation == "resolve" and not policy["allow_human_resolution"]:
        return VisionAccessDecision(False, "vision_human_resolution_disabled", auth_source, policy)
    if auth_source == "unknown":
        return VisionAccessDecision(False, "vision_auth_source_unknown", auth_source, policy)
    return VisionAccessDecision(True, "ok", auth_source, policy)


# ---------------------------------------------------------------------------------------------------------
# Request validation


def decode_data_uri_images(items: Any, *, max_media: int, max_image_bytes: int) -> list[bytes]:
    """Decode ``data:image/...;base64,`` URIs; URLs and anything else are rejected before decoding."""
    if not isinstance(items, list) or not items:
        raise VisionRequestError("vision_decision.images_required", "images must be a non-empty list of data URIs")
    if len(items) > max_media:
        raise VisionRequestError("vision_decision.too_many_images", "request exceeds VISION_DECISION_MAX_MEDIA")
    max_encoded = 4 * ((max_image_bytes + 2) // 3)
    images: list[bytes] = []
    for item in items:
        match = _DATA_URI.match(item) if isinstance(item, str) else None
        if match is None:
            raise VisionRequestError("vision_decision.invalid_image", "images must be data:image/png|jpeg|webp;base64 URIs")
        encoded = item[match.end() :]
        if len(encoded) > max_encoded:
            raise VisionRequestError("vision_decision.image_too_large", "image exceeds VISION_DECISION_MAX_IMAGE_BYTES")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise VisionRequestError("vision_decision.invalid_image", "image is not valid base64") from exc
        if not content:
            raise VisionRequestError("vision_decision.invalid_image", "image is empty")
        if len(content) > max_image_bytes:
            raise VisionRequestError("vision_decision.image_too_large", "image exceeds VISION_DECISION_MAX_IMAGE_BYTES")
        images.append(content)
    return images


def max_request_bytes(config: VisionDecisionConfig) -> int:
    """Upper bound for a JSON body carrying ``max_media`` base64 images plus text."""
    return config.max_media * (4 * ((config.max_image_bytes + 2) // 3) + 64) + 64 * 1024


# ---------------------------------------------------------------------------------------------------------
# Actions


def _directive(kind: str, key: str, value: str) -> Handler:
    def handler(_invocation: VoiceCommandInvocation) -> Mapping[str, Any]:
        return {"kind": kind, key: value}

    return handler


def default_vision_action_handlers() -> dict[str, Handler]:
    """Hub handlers for the task policy's ``vision.*`` action types; each yields a typed client directive."""
    handlers: dict[str, Handler] = {}
    for orientation in ("upright", "rotated_90", "rotated_180", "rotated_270"):
        handlers[f"vision.document.rotate.{orientation}"] = _directive("rotate_page", "orientation", orientation)
    for kind in ("invoice", "receipt", "letter", "form", "photo", "other"):
        handlers[f"vision.document.route.{kind}"] = _directive("route_document", "queue", kind)
    return handlers


# ---------------------------------------------------------------------------------------------------------
# Runtime


@dataclass
class VisionHubRuntime:
    provider: VisionDecisionProvider
    escalations: VisionEscalationExecutor
    executor: VoiceCommandExecutor
    confirmations: VoiceCommandConfirmationStore
    policy: VisionTaskPolicy = field(default_factory=VisionTaskPolicy)
    confirmation_ttl_seconds: float = 30.0


# Stores outlive a configuration change: an issued id stays single-use.
_confirmations = VoiceCommandConfirmationStore()
_tickets = VoiceCommandConfirmationStore(max_pending=256)
_executor = VoiceCommandExecutor(default_vision_action_handlers())
_runtime_lock = threading.Lock()
_runtime_cache: tuple[tuple[str | None, ...], VisionHubRuntime] | None = None


def get_vision_hub_runtime(environ: Mapping[str, str] | None = None) -> VisionHubRuntime | None:
    """``None`` when VisionDecision is off (nothing else is read). Raises on misconfiguration."""
    global _runtime_cache
    env = os.environ if environ is None else environ
    provider = get_vision_decision_provider(env)
    if provider is None:
        return None
    # The provider is cached per decision env; rebuild when it or an escalation setting changes.
    key = tuple(env.get(name) for name in _ESCALATION_ENV_KEYS + ("VOICE_AUDIO_DECISION_CONFIRM_TTL_SECONDS",))
    with _runtime_lock:
        if _runtime_cache is None or _runtime_cache[0] != key or _runtime_cache[1].provider is not provider:
            escalation = VisionEscalationConfig.from_env(provider.config, env)
            _runtime_cache = (
                key,
                VisionHubRuntime(
                    provider=provider,
                    escalations=build_escalation_executor(provider.config, escalation, tickets=_tickets),
                    executor=_executor,
                    confirmations=_confirmations,
                    confirmation_ttl_seconds=confirmation_ttl_seconds(env),
                ),
            )
        return _runtime_cache[1]


# ---------------------------------------------------------------------------------------------------------
# Dispatch


@dataclass(frozen=True)
class VisionHubResult:
    response: dict[str, Any]
    audit: dict[str, Any]
    status_code: int = 200


def _execution_audit(execution: VoiceCommandExecution | None) -> dict[str, Any] | None:
    # The action type repeats the value (e.g. ``vision.document.route.invoice``): keep it out of audit.
    if execution is None:
        return None
    return {"status": "executed" if execution.executed else "denied", "error_code": execution.error_code}


class _FieldDispatcher:
    def __init__(self, runtime: VisionHubRuntime, task: VisionTask, principal: VoicePrincipal, authorize: Authorize):
        self.runtime = runtime
        self.task = task
        self.principal = principal
        self.authorize = authorize

    def act(self, name: str, value: Any, action: VisionTaskAction | None) -> tuple[dict[str, Any], dict[str, Any]]:
        """Policy-allowed value: release it (informational) or run the action (exposure re-checked)."""
        if action is None:
            return {"hub_action": VisionHubAction.ACT.value, "value": value}, {"hub_action": "act"}
        execution = self.runtime.executor.execute(
            VoiceCommandInvocation(
                action_type=action.action_type, command=value, profile=self.task.task_id, field=name, principal=self.principal
            ),
            authorize=self.authorize,
        )
        if not execution.executed:
            return self._denied(execution)
        return (
            {"hub_action": VisionHubAction.ACT.value, "value": value, "action_type": action.action_type, "execution": execution.as_response()},
            {"hub_action": "act", "execution": _execution_audit(execution)},
        )

    def confirm(self, name: str, value: Any, action: VisionTaskAction | None) -> tuple[dict[str, Any], dict[str, Any]]:
        """Value needs a confirmation: an action only runs through the explicit confirm route."""
        if action is None:
            return {"hub_action": VisionHubAction.CONFIRM.value, "value": value}, {"hub_action": "confirm"}
        if not self.runtime.executor.supports(action.action_type):
            return self._denied(VoiceCommandExecution(False, action.action_type, "executor.unknown_action_type"))
        try:
            pending = self.runtime.confirmations.issue(
                self.principal,
                action_type=action.action_type,
                command=value,
                profile=self.task.task_id,
                field_name=name,
                ttl_seconds=self.runtime.confirmation_ttl_seconds,
            )
        except ConfirmationError as exc:
            return self._denied(VoiceCommandExecution(False, action.action_type, exc.code))
        return (
            {
                "hub_action": VisionHubAction.CONFIRM.value,
                "value": value,
                "action_type": action.action_type,
                "confirmation": {
                    "confirmation_id": pending.confirmation_id,
                    "action_type": pending.action_type,
                    "expires_in_seconds": self.runtime.confirmation_ttl_seconds,
                    "confirm_route": CONFIRM_ROUTE,
                    "single_use": True,
                    "grants_permission": False,
                },
            },
            {"hub_action": "confirm", "confirmation_issued": True},
        )

    @staticmethod
    def _denied(execution: VoiceCommandExecution) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            {"hub_action": VisionHubAction.DENY.value, "error_code": execution.error_code, "execution": execution.as_response()},
            {"hub_action": "deny", "error_code": execution.error_code, "execution": _execution_audit(execution)},
        )

    def dispatch(self, name: str, value: Any, verdict: VisionPolicyVerdict, action: VisionTaskAction | None):
        if verdict is VisionPolicyVerdict.ALLOW:
            return self.act(name, value, action)
        if verdict is VisionPolicyVerdict.CONFIRM:
            return self.confirm(name, value, action)
        return {"hub_action": VisionHubAction.DENY.value}, {"hub_action": "deny"}


def _scores(decision: VisionFieldDecision) -> dict[str, Any]:
    return {
        "probability": decision.probability,
        "margin": decision.margin,
        "entropy": decision.entropy,
        "abstain": decision.abstain,
    }


def run_vision_decision(
    runtime: VisionHubRuntime,
    *,
    task_id: str,
    images: Sequence[bytes],
    text: str,
    principal: VoicePrincipal,
    authorize: Authorize,
    deadline_seconds: float | None = None,
) -> VisionHubResult:
    task = runtime.policy.task(task_id)
    if task is None:
        raise VisionRequestError("vision_decision.task_not_supported", "task is not in the vision task catalog")
    config = runtime.provider.config
    if config.schemas and task.task_id not in config.schemas:
        raise VisionRequestError("vision_decision.task_not_enabled", "task is not in VISION_DECISION_SCHEMAS")
    deadline = time.monotonic() + deadline_seconds if deadline_seconds is not None else None
    outcome = runtime.provider.decide(
        task.schema,
        [VisionContext(images=tuple(VisionImage(content) for content in images), text=text)],
        deadline_monotonic=deadline,
    )
    audit: dict[str, Any] = {"task": task.task_id, "image_count": len(images), "outcome": outcome.as_audit_dict()}
    base = {"enabled": True, "task": task.task_id, "grants_permission": False, "provenance": outcome.provenance()}
    if not outcome.ok and outcome.error_code in LOCAL_REJECTION_CODES:
        audit["hub_action"] = "rejected"
        return VisionHubResult(
            response={**base, "hub_action": "rejected", "error_code": outcome.error_code, "fields": {}},
            audit=audit,
            status_code=422,
        )

    recorded: dict[str, VisionTaskPolicyDecision] = {}

    def policy(proposal: VisionProposal) -> VisionPolicyVerdict:
        decision = runtime.policy.decide_proposal(proposal)
        recorded[proposal.field] = decision
        return decision.verdict

    hub = gate_vision_decision(
        outcome,
        policy=policy,
        escalation_target=task.escalation_target,
        human_review_fields=task.human_review_fields,
    )
    if hub.kind is VisionDecisionKind.NO_DECISION:
        audit.update({"hub_action": "normal_path", "error_code": hub.error_code})
        return VisionHubResult(
            response={**base, "hub_action": "normal_path", "error_code": hub.error_code, "http_status": hub.http_status, "fields": {}},
            audit=audit,
        )

    dispatcher = _FieldDispatcher(runtime, task, principal, authorize)
    fields: dict[str, dict[str, Any]] = {}
    field_audit: dict[str, dict[str, Any]] = {}
    for name, decision in hub.fields.items():
        if decision.kind is VisionDecisionKind.ESCALATE:
            continue
        record = recorded.get(name)
        rule = record.rule if record is not None else "policy_error"
        action = record.action if record is not None else None
        # The gate already turned policy errors and unknown verdicts into DENY.
        verdict = {
            VisionHubAction.ACT: VisionPolicyVerdict.ALLOW,
            VisionHubAction.CONFIRM: VisionPolicyVerdict.CONFIRM,
        }.get(decision.action, VisionPolicyVerdict.DENY)
        out, out_audit = dispatcher.dispatch(name, decision.value, verdict, action)
        fields[name] = {**_scores(decision), "rule": rule, "grants_permission": False, **out}
        field_audit[name] = {**_scores(decision), "rule": rule, **out_audit}

    escalations = hub.escalations
    if escalations:
        results = runtime.escalations.escalate(
            task, escalations, principal=principal, images=images, text=text, deadline_monotonic=deadline
        )
        for name, decision in hub.fields.items():
            if decision.kind is not VisionDecisionKind.ESCALATE:
                continue
            result = results.get(name)
            entry: dict[str, Any] = {
                **_scores(decision),
                "hub_action": VisionHubAction.ESCALATE.value,
                "value": None,
                "rule": None,
                "grants_permission": False,
                "escalation": result.as_response() if result is not None else None,
                "suggestion": None,
            }
            entry_audit: dict[str, Any] = {
                **_scores(decision),
                "hub_action": "escalate",
                "escalation": result.as_audit_dict() if result is not None else None,
            }
            if result is not None and result.status == "answered":
                suggestion = runtime.policy.decide(
                    task_id=task.task_id,
                    field_name=name,
                    value=result.suggestion,
                    probability=None,
                    model=result.answered_by_model,
                    source="chat",
                )
                # An unscored answer is at most a confirmation, whatever the policy says.
                verdict = VisionPolicyVerdict.DENY if suggestion.verdict is VisionPolicyVerdict.DENY else VisionPolicyVerdict.CONFIRM
                out, out_audit = dispatcher.dispatch(name, result.suggestion, verdict, suggestion.action)
                entry["suggestion"] = {"rule": suggestion.rule, "grants_permission": False, **out}
                entry_audit["suggestion"] = {"rule": suggestion.rule, **out_audit}
            fields[name] = entry
            field_audit[name] = entry_audit

    hub_action = "escalate" if escalations else "decided"
    audit.update({"hub_action": hub_action, "fields": field_audit})
    return VisionHubResult(response={**base, "hub_action": hub_action, "error_code": None, "fields": fields}, audit=audit)


def confirm_vision_action(
    runtime: VisionHubRuntime,
    confirmation_id: str,
    *,
    principal: VoicePrincipal,
    action_type: str,
    confirmed: bool,
    authorize: Authorize,
) -> VoiceCommandExecution:
    """Single use, bound to principal and action; only ``confirmed is True`` executes."""
    return confirm_voice_command(
        confirmation_id,
        principal=principal,
        action_type=action_type,
        confirmed=confirmed,
        authorize=authorize,
        executor=runtime.executor,
        confirmations=runtime.confirmations,
    )


def resolve_escalation(
    runtime: VisionHubRuntime,
    escalation_id: str,
    *,
    task_id: str,
    field_name: str,
    value: Any,
    principal: VoicePrincipal,
    authorize: Authorize,
) -> VisionHubResult:
    """A human answers a review ticket. Any use consumes it; the answer still passes the task policy."""
    tickets = runtime.escalations.tickets
    try:
        tickets.consume(escalation_id, principal, action_type=escalation_action_type(task_id, field_name))
    except ConfirmationError as exc:
        status = 410 if exc.code == "confirmation.expired" else 403
        code = exc.code.replace("confirmation.", "escalation.")
        return VisionHubResult(
            response={"hub_action": "deny", "error_code": code, "grants_permission": False},
            audit={"task": task_id, "field": field_name, "hub_action": "deny", "error_code": code},
            status_code=status,
        )
    decision = runtime.policy.decide(
        task_id=task_id, field_name=field_name, value=value, probability=None, model=None, source="human"
    )
    task = runtime.policy.task(task_id)
    audit: dict[str, Any] = {"task": task_id, "field": field_name, "rule": decision.rule}
    if decision.verdict is not VisionPolicyVerdict.ALLOW or task is None:
        audit["hub_action"] = "deny"
        return VisionHubResult(
            response={"hub_action": "deny", "rule": decision.rule, "error_code": "escalation.value_denied", "grants_permission": False},
            audit=audit,
            status_code=422,
        )
    out, out_audit = _FieldDispatcher(runtime, task, principal, authorize).act(field_name, value, decision.action)
    audit.update(out_audit)
    status = 200 if out["hub_action"] == VisionHubAction.ACT.value else 403
    return VisionHubResult(
        response={"field": field_name, "rule": decision.rule, "grants_permission": False, **out},
        audit=audit,
        status_code=status,
    )
