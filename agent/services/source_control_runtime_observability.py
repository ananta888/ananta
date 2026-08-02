"""Operational decorator for canonical Source Control Center runtime calls."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Protocol

from agent.services.source_control_observability import (
    SourceControlAuditEvent,
    SourceControlAuditOperation,
    SourceControlDecision,
    SourceControlHealthMetricsPublisher,
    SourceControlHealthMonitor,
    SourceControlMetricsPort,
    bounded_metric_labels,
    emit_source_control_audit,
)
from agent.services.source_control_rollout_policy import (
    SourceControlRolloutPolicy,
    SourceControlShadowComparator,
    SourceControlShadowProjectionComparator,
)

_LOG = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_AUDITED_METHODS = frozenset(
    {
        "validate_connection",
        "create_connection",
        "validate_content_admission",
        "create_content_admission",
        "create_grant",
        "revoke_grant",
        "prepare_index_access",
        "mutate",
        "dispatch_operation",
        "bulk_execute",
        "access_preview",
        "context_policy_draft",
        "context_policy_lint",
        "context_policy_preview",
        "context_policy_transition",
        "context_policy_rollback",
    }
)
_SHADOW_METHODS = frozenset(
    {
        "list_connections",
        "get_connection",
        "access_preview",
        "context_policy_preview",
    }
)


class SourceControlShadowObservationPort(Protocol):
    """Legacy observer gets only scope and object IDs, never request content."""

    def observe(
        self,
        *,
        operation: str,
        tenant_id: str,
        project_id: str,
        resource_kind: str,
        resource_id: str,
    ) -> Mapping[str, str] | None: ...


class SourceControlRuntimeObservability:
    """Decorate a runtime without changing its authorization decisions."""

    def __init__(
        self,
        delegate: object,
        *,
        rollout: SourceControlRolloutPolicy,
        metrics: SourceControlMetricsPort,
        health: SourceControlHealthMonitor,
        shadow: SourceControlShadowObservationPort | None = None,
        audit_emitter: Callable[[SourceControlAuditEvent], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        trace_id: Callable[[], str] | None = None,
    ) -> None:
        self._delegate = delegate
        self._rollout = rollout
        self._metrics = metrics
        self._health = health
        self._health_metrics = SourceControlHealthMetricsPublisher(metrics)
        self._shadow = shadow
        self._audit = audit_emitter or emit_source_control_audit
        self._clock = clock
        self._trace_id = trace_id or _request_trace_id

    def __getattr__(self, name: str):
        target = getattr(self._delegate, name)
        if name.startswith("_") or not callable(target):
            return target

        @wraps(target)
        def observed(*args: object, **kwargs: object):
            started = self._clock()
            operation = _operation(name, kwargs)
            try:
                self._require_capability(name, kwargs)
                result = target(*args, **kwargs)
            except Exception as exc:
                reason = _reason_code(exc)
                decision = (
                    SourceControlDecision.unavailable
                    if int(getattr(exc, "status_code", 500)) >= 500
                    else SourceControlDecision.deny
                )
                self._health.record_failure(reason)
                self._record(
                    name=name,
                    operation=operation,
                    kwargs=kwargs,
                    result=None,
                    decision=decision,
                    reason_code=reason,
                    status="failed",
                    duration=max(self._clock() - started, 0.0),
                )
                raise
            decision, reason = _result_decision(result)
            self._health.record_result(operation=name, result=result)
            self._record(
                name=name,
                operation=operation,
                kwargs=kwargs,
                result=result,
                decision=decision,
                reason_code=reason,
                status="completed",
                duration=max(self._clock() - started, 0.0),
            )
            self._shadow_compare(
                name=name,
                operation=operation,
                kwargs=kwargs,
                result=result,
            )
            return result

        return observed

    @property
    def health_monitor(self) -> SourceControlHealthMonitor:
        return self._health

    @property
    def delegate(self) -> object:
        return self._delegate

    def _require_capability(
        self, name: str, kwargs: Mapping[str, object]
    ) -> None:
        capability = None
        if name in {
            "create_connection",
            "create_content_admission",
            "bulk_execute",
            "context_policy_draft",
            "context_policy_transition",
            "context_policy_rollback",
            "mutate",
        }:
            capability = "persistent_sources"
        elif name in {
            "create_grant",
            "revoke_grant",
            "prepare_index_access",
        }:
            capability = "local_grants"
        elif name == "dispatch_operation":
            capability = (
                "workspace_indexing"
                if str(kwargs.get("operation") or "") == "run"
                else "persistent_sources"
            )
        if capability is not None:
            self._rollout.require(capability)

    def _record(
        self,
        *,
        name: str,
        operation: str,
        kwargs: Mapping[str, object],
        result: object,
        decision: SourceControlDecision,
        reason_code: str,
        status: str,
        duration: float,
    ) -> None:
        safe_operation = _metric_operation(operation)
        safe_reason = _metric_reason(reason_code)
        try:
            self._metrics.observe_duration(
                "source_control_operation_duration_seconds",
                duration,
                bounded_metric_labels(
                    operation=safe_operation,
                    status=status,
                ),
            )
            self._metrics.increment(
                "source_control_operations_total",
                bounded_metric_labels(
                    operation=safe_operation,
                    decision=decision.value,
                    reason_code=safe_reason,
                    status=status,
                ),
            )
        except Exception:
            self._health.set_operational_alarm("metrics_adapter_failure")
            _LOG.error("source_control_metrics_adapter_failure", exc_info=True)
        if name in _AUDITED_METHODS:
            try:
                self._audit(
                    _audit_event(
                        operation=operation,
                        kwargs=kwargs,
                        result=result,
                        decision=decision,
                        reason_code=reason_code,
                        trace_id=self._trace_id(),
                    )
                )
            except Exception:
                self._health.set_operational_alarm("audit_adapter_failure")
                _LOG.error("source_control_audit_adapter_failure", exc_info=True)
        self._publish_health()

    def _shadow_compare(
        self,
        *,
        name: str,
        operation: str,
        kwargs: Mapping[str, object],
        result: object,
    ) -> None:
        config = self._rollout.configuration
        if not config.shadow_compare_enabled or name not in _SHADOW_METHODS:
            return
        principal = kwargs.get("principal")
        tenant_id, project_id, _actor_id = _principal_scope(principal)
        resource_kind, resource_id = _resource(kwargs)
        if self._shadow is None:
            self._health.set_operational_alarm("shadow_backend_unavailable")
            self._shadow_metric(operation, "unavailable", "unavailable")
            return
        try:
            legacy = self._shadow.observe(
                operation=operation,
                tenant_id=tenant_id,
                project_id=project_id,
                resource_kind=resource_kind,
                resource_id=resource_id,
            )
            if legacy is None:
                self._health.set_operational_alarm("shadow_result_unavailable")
                self._shadow_metric(operation, "unavailable", "unavailable")
                return
            if name in {"access_preview", "context_policy_preview"}:
                canonical_decision, canonical_reason = _result_decision(result)
                difference = SourceControlShadowComparator().compare(
                    operation=_metric_operation(operation),
                    legacy=legacy,
                    canonical={
                        "decision": canonical_decision.value,
                        "reason_code": canonical_reason,
                    },
                )
                canonical_digest = _digest(
                    {
                        "decision": canonical_decision.value,
                        "reason_code": canonical_reason,
                    }
                )
            else:
                canonical_digest = _digest(result)
                difference = SourceControlShadowProjectionComparator().compare(
                    operation=_metric_operation(operation),
                    legacy_digest=str(legacy.get("projection_digest") or ""),
                    canonical_digest=canonical_digest,
                )
            if difference is None:
                self._shadow_metric(operation, "allow", "matched")
                return
            self._shadow_metric(operation, "deny", "different")
            self._health.set_operational_alarm("shadow_difference")
            from agent.common.audit import log_audit

            details = {
                "tenant_id": _opaque(tenant_id, "tenant"),
                "project_id": _opaque(project_id, "project"),
                "operation": _metric_operation(operation),
                "resource_kind": _opaque(resource_kind, "resource"),
                "resource_id": _opaque(resource_id, "collection"),
                "canonical_digest": canonical_digest,
                "legacy_digest": str(
                    legacy.get("projection_digest") or _digest(legacy)
                ),
                "trace_id": _opaque(self._trace_id(), "trace"),
            }
            log_audit("source_control.shadow_difference", details)
        except Exception:
            self._health.set_operational_alarm("shadow_comparison_failure")
            self._shadow_metric(operation, "unavailable", "failed")
            _LOG.error("source_control_shadow_comparison_failure", exc_info=True)

    def _shadow_metric(
        self, operation: str, decision: str, status: str
    ) -> None:
        try:
            self._metrics.increment(
                "source_control_shadow_differences_total",
                bounded_metric_labels(
                    operation=_metric_operation(operation),
                    decision=decision,
                    status=status,
                ),
            )
        except Exception:
            self._health.set_operational_alarm("metrics_adapter_failure")

    def _publish_health(self) -> None:
        report = self._health.snapshot()
        try:
            self._health_metrics.publish(report)
        except Exception:
            self._health.set_operational_alarm("metrics_adapter_failure")


def _request_trace_id() -> str:
    try:
        from flask import g, has_request_context, request

        if has_request_context():
            return _opaque(
                request.headers.get("X-Trace-ID")
                or getattr(g, "trace_id", "")
                or request.headers.get("X-Request-ID")
                or "",
                "trace",
            )
    except Exception:
        pass
    return "trace-internal"


def _operation(name: str, kwargs: Mapping[str, object]) -> str:
    if name in {"dispatch_operation", "mutate", "context_policy_transition"}:
        value = str(kwargs.get("operation") or name)
        return "index" if value == "run" else value
    return {
        "validate_connection": "validate",
        "create_connection": "create",
        "validate_content_admission": "validate",
        "create_content_admission": "scan",
        "create_grant": "grant",
        "revoke_grant": "deny",
        "bulk_execute": "lifecycle",
        "context_policy_draft": "approval",
        "context_policy_lint": "validate",
        "context_policy_preview": "approval",
        "context_policy_rollback": "rollback",
    }.get(name, name)


def _audit_operation(value: str) -> SourceControlAuditOperation:
    normalized = _metric_operation(value)
    try:
        return SourceControlAuditOperation(normalized)
    except ValueError:
        return SourceControlAuditOperation.lifecycle


def _principal_scope(principal: object) -> tuple[str, str, str]:
    return (
        str(getattr(principal, "tenant_id", "") or ""),
        str(getattr(principal, "project_id", "") or ""),
        str(
            getattr(
                principal,
                "subject_id",
                getattr(principal, "actor_id", ""),
            )
            or ""
        ),
    )


def _resource(kwargs: Mapping[str, object]) -> tuple[str, str]:
    candidates = (
        ("source_connection", "connection_id"),
        ("source_access_grant", "grant_id"),
        ("knowledge_index", "index_id"),
        ("context_policy", "policy_id"),
        ("source_control_resource", "resource_id"),
    )
    for kind, key in candidates:
        value = kwargs.get(key)
        if isinstance(value, str) and value:
            return kind, value
    payload = kwargs.get("payload")
    if isinstance(payload, Mapping):
        value = payload.get("source_revision_id")
        if isinstance(value, str) and value:
            return "source_revision", value
    return "source_control_collection", "collection"


def _result_decision(
    result: object,
) -> tuple[SourceControlDecision, str]:
    value = result[0] if isinstance(result, tuple) and result else result
    mapping = value if isinstance(value, Mapping) else {}
    decision_value = str(mapping.get("decision") or "allow")
    reason = str(mapping.get("reason_code") or "completed")
    try:
        decision = SourceControlDecision(decision_value)
    except ValueError:
        decision = SourceControlDecision.allow
    return decision, _safe_reason(reason)


def _reason_code(exc: Exception) -> str:
    return _safe_reason(
        str(getattr(exc, "reason_code", "") or "internal_error")
    )


def _safe_reason(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if _REASON.fullmatch(normalized) else "reason_other"


def _metric_reason(value: str) -> str:
    normalized = _safe_reason(value)
    categories = (
        ("authorization", ("auth", "credential", "forbidden", "scope")),
        ("validation", ("invalid", "required", "forbidden_field")),
        ("not_found", ("not_found", "missing")),
        ("conflict", ("conflict", "idempotency", "etag", "version")),
        ("unavailable", ("unavailable", "timeout")),
        ("approval", ("approval",)),
        ("blocked", ("blocked", "deny")),
    )
    for category, markers in categories:
        if any(marker in normalized for marker in markers):
            return category
    return "completed" if normalized == "completed" else "other"


def _metric_operation(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    allowed = {
        "activate",
        "approval",
        "create",
        "deny",
        "disable",
        "get_connection",
        "grant",
        "index",
        "lifecycle",
        "list_connections",
        "purge",
        "refresh",
        "rollback",
        "scan",
        "tombstone",
        "validate",
    }
    return normalized if normalized in allowed else "lifecycle"


def _opaque(value: str, fallback: str) -> str:
    text = str(value or "")
    if _IDENTIFIER.fullmatch(text):
        return text
    if not text:
        return fallback
    return f"{fallback}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"


def _digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=lambda item: getattr(item, "__dict__", str(item)),
        ).encode("ascii")
    except Exception:
        encoded = type(value).__name__.encode("ascii", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def _audit_event(
    *,
    operation: str,
    kwargs: Mapping[str, object],
    result: object,
    decision: SourceControlDecision,
    reason_code: str,
    trace_id: str,
) -> SourceControlAuditEvent:
    tenant_id, project_id, actor_id = _principal_scope(
        kwargs.get("principal")
    )
    resource_kind, resource_id = _resource(kwargs)
    mapping = (
        result[0]
        if isinstance(result, tuple) and result
        else result
    )
    values = mapping if isinstance(mapping, Mapping) else {}

    def digest_field(*names: str) -> str | None:
        for name in names:
            candidate = values.get(name)
            if (
                isinstance(candidate, str)
                and len(candidate) == 64
                and all(char in "0123456789abcdef" for char in candidate)
            ):
                return candidate
        return None

    return SourceControlAuditEvent(
        operation=_audit_operation(operation),
        actor_id=_opaque(actor_id, "actor"),
        tenant_id=_opaque(tenant_id, "tenant"),
        project_id=_opaque(project_id, "project"),
        resource_kind=_opaque(resource_kind, "resource"),
        resource_id=_opaque(resource_id, "collection"),
        trace_id=_opaque(trace_id, "trace"),
        decision=decision,
        reason_code=_safe_reason(reason_code),
        revision_digest=digest_field("revision_digest"),
        manifest_digest=digest_field(
            "manifest_digest", "content_manifest_digest"
        ),
        policy_digest=digest_field(
            "policy_digest", "policy_snapshot_digest"
        ),
    )


__all__ = [
    "SourceControlRuntimeObservability",
    "SourceControlShadowObservationPort",
]
