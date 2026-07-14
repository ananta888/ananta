"""Domain contracts for the Hub workflow-runtime operations read model.

The contracts are deliberately framework- and transport-neutral.  They contain
only evaluated, redacted operational facts; worker or Temporal client objects
must never cross this boundary.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from agent.services.identity_validation import require_canonical_identity
from agent.services.workflow_runtime._serialization import redact_json

RUNTIME_OPERATIONS_RECORD_SCHEMA = "ananta.workflow_runtime_operations_record.v1"
SUCCESS_STATES = frozenset({"completed", "succeeded", "success"})
TERMINAL_STATES = frozenset({*SUCCESS_STATES, "failed", "cancelled"})

_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(api[-_ ]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[:=]\s*)[^\s,;]+"),
)


def _safe_text(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    for pattern in _SECRET_TEXT_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    return text[:limit]


def _safe_id(value: Any, *, limit: int = 160) -> str:
    return _safe_text(value, limit=limit)


def _strict_identity(
    value: Any,
    *,
    field_name: str,
    required: bool = True,
    limit: int = 160,
) -> str:
    """Validate persistence and authorization identities without mutation.

    Truncating or trimming an identity can make two distinct tenant/run keys
    indistinguishable. Presentation text remains bounded by ``_safe_text``;
    security-relevant keys instead fail closed.
    """

    return require_canonical_identity(
        value,
        field_name=field_name,
        required=required,
        max_length=limit,
    )


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mapping_items(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_items(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _strict_identity_items(
    value: Any,
    *,
    field_name: str,
    limit: int = 160,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(
        _strict_identity(
            item,
            field_name=field_name,
            limit=limit,
        )
        for item in value
    )


def _identity_alias(value: Mapping[str, Any], primary: str, fallback: str) -> Any:
    """Resolve a compatibility alias without hiding an invalid primary value."""

    primary_value = value.get(primary)
    if primary_value not in (None, ""):
        return primary_value
    return value.get(fallback)


@dataclass(frozen=True)
class RuntimeCapabilityView:
    name: str
    status: str = "supported"
    reason_code: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "RuntimeCapabilityView":
        if isinstance(value, Mapping):
            return cls(
                name=_safe_id(value.get("name") or value.get("capability")),
                status=_safe_id(value.get("status") or "supported", limit=48).lower(),
                reason_code=_safe_id(value.get("reason_code")),
            )
        return cls(name=_safe_id(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "reason_code": self.reason_code or None,
        }


@dataclass(frozen=True)
class RuntimeFallbackView:
    source_runtime: str
    target_runtime: str
    reason_code: str
    semantic_class: str = "unknown"
    approved: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeFallbackView":
        return cls(
            source_runtime=_safe_id(value.get("source_runtime") or value.get("from"), limit=64),
            target_runtime=_safe_id(value.get("target_runtime") or value.get("to"), limit=64),
            reason_code=_safe_id(value.get("reason_code") or "fallback_observed"),
            semantic_class=_safe_id(value.get("semantic_class") or "unknown", limit=64),
            approved=bool(value.get("approved", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_runtime": self.source_runtime,
            "target_runtime": self.target_runtime,
            "reason_code": self.reason_code,
            "semantic_class": self.semantic_class,
            "approved": self.approved,
        }


@dataclass(frozen=True)
class RuntimeRecoveryView:
    status: str = "none"
    strategy: str = ""
    attempts: int = 0
    last_checkpoint_ref: str = ""
    reason_code: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "RuntimeRecoveryView":
        raw = value or {}
        return cls(
            status=_safe_id(raw.get("status") or "none", limit=48).lower(),
            strategy=_safe_id(raw.get("strategy"), limit=96),
            attempts=max(0, _as_int(raw.get("attempts"))),
            last_checkpoint_ref=_safe_id(raw.get("last_checkpoint_ref") or raw.get("checkpoint_ref")),
            reason_code=_safe_id(raw.get("reason_code")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "strategy": self.strategy or None,
            "attempts": self.attempts,
            "last_checkpoint_ref": self.last_checkpoint_ref or None,
            "reason_code": self.reason_code or None,
        }


@dataclass(frozen=True)
class RuntimeGateView:
    gate_id: str
    label: str
    status: str = "open"
    approval_id: str = ""
    required_evidence_refs: tuple[str, ...] = ()
    allowed_commands: tuple[str, ...] = ()
    expires_at: float | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeGateView":
        expires_at = value.get("expires_at")
        return cls(
            gate_id=_strict_identity(
                _identity_alias(value, "gate_id", "id"),
                field_name="gate_id",
                required=False,
            ),
            label=_safe_text(value.get("label") or value.get("gate_id") or value.get("id"), limit=200),
            status=_safe_id(value.get("status") or "open", limit=48).lower(),
            approval_id=_strict_identity(
                _identity_alias(value, "approval_id", "approval_ref"),
                field_name="approval_id",
                required=False,
            ),
            required_evidence_refs=tuple(
                sorted(
                    set(
                        _strict_identity_items(
                            value.get("required_evidence_refs"),
                            field_name="evidence_ref",
                        )
                    )
                )
            ),
            allowed_commands=tuple(
                sorted({_safe_id(item, limit=64) for item in _string_items(value.get("allowed_commands"))})
            ),
            expires_at=_as_float(expires_at) if expires_at is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "label": self.label,
            "status": self.status,
            "approval_id": self.approval_id or None,
            "required_evidence_refs": list(self.required_evidence_refs),
            "allowed_commands": list(self.allowed_commands),
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class RuntimeEvidenceView:
    evidence_id: str
    kind: str
    verification_status: str = "unverified"
    summary: str = ""
    source_ref: str = ""
    observed_at: float = field(default_factory=time.time)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeEvidenceView":
        return cls(
            evidence_id=_strict_identity(
                _identity_alias(value, "evidence_id", "id"),
                field_name="evidence_id",
                required=False,
            ),
            kind=_safe_id(value.get("kind") or value.get("type") or "runtime", limit=64),
            verification_status=_safe_id(
                value.get("verification_status") or value.get("status") or "unverified",
                limit=48,
            ).lower(),
            summary=_safe_text(value.get("summary") or value.get("message")),
            source_ref=_safe_id(value.get("source_ref") or value.get("artifact_ref")),
            observed_at=max(0.0, _as_float(value.get("observed_at") or value.get("timestamp"), time.time())),
        )

    @property
    def verified(self) -> bool:
        return self.verification_status == "verified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "verification_status": self.verification_status,
            "summary": self.summary,
            "source_ref": self.source_ref or None,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class RuntimeGapView:
    code: str
    category: str
    severity: str = "warning"
    summary: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeGapView":
        return cls(
            code=_safe_id(value.get("code") or value.get("reason_code") or "runtime_gap"),
            category=_safe_id(value.get("category") or "parity", limit=64),
            severity=_safe_id(value.get("severity") or "warning", limit=32).lower(),
            summary=_safe_text(value.get("summary") or value.get("message")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "severity": self.severity,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class WorkflowRuntimeOperationRecord:
    tenant_id: str
    run_id: str
    workflow_id: str
    task_id: str
    runtime: str
    mode: str
    status: str
    capabilities: tuple[RuntimeCapabilityView, ...] = ()
    fallbacks: tuple[RuntimeFallbackView, ...] = ()
    cost_micros: int = 0
    latency_ms: float = 0.0
    recovery: RuntimeRecoveryView = field(default_factory=RuntimeRecoveryView)
    gates: tuple[RuntimeGateView, ...] = ()
    evidence: tuple[RuntimeEvidenceView, ...] = ()
    parity_gaps: tuple[RuntimeGapView, ...] = ()
    semantic_deviations: tuple[RuntimeGapView, ...] = ()
    updated_at: float = field(default_factory=time.time)
    stale_after_seconds: float = 60.0
    source_sequence: int = 0
    explicitly_degraded: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WorkflowRuntimeOperationRecord":
        tenant_id = _strict_identity(value.get("tenant_id"), field_name="tenant_id")
        run_id = _strict_identity(value.get("run_id"), field_name="run_id")
        runtime = _safe_id(value.get("runtime") or value.get("runtime_kind"), limit=64).lower()
        mode = _safe_id(value.get("mode") or value.get("execution_mode"), limit=64).lower()
        if not runtime or not mode:
            raise ValueError("runtime and mode are required")

        capability_values = value.get("capabilities") or ()
        if isinstance(capability_values, Mapping):
            capability_values = [
                {"name": name, **(details if isinstance(details, Mapping) else {"status": details})}
                for name, details in capability_values.items()
            ]
        elif not isinstance(capability_values, (list, tuple, set, frozenset)):
            capability_values = [capability_values] if capability_values else []
        return cls(
            tenant_id=tenant_id,
            run_id=run_id,
            workflow_id=_strict_identity(
                value.get("workflow_id"),
                field_name="workflow_id",
                required=False,
            ),
            task_id=_strict_identity(
                _identity_alias(value, "task_id", "hub_task_id"),
                field_name="task_id",
                required=False,
            ),
            runtime=runtime,
            mode=mode,
            status=_safe_id(value.get("status") or "pending", limit=64).lower(),
            capabilities=tuple(
                capability
                for capability in (RuntimeCapabilityView.from_value(item) for item in capability_values)
                if capability.name
            ),
            fallbacks=tuple(RuntimeFallbackView.from_mapping(item) for item in _mapping_items(value.get("fallbacks"))),
            cost_micros=max(0, _as_int(value.get("cost_micros"))),
            latency_ms=max(0.0, _as_float(value.get("latency_ms"))),
            recovery=RuntimeRecoveryView.from_mapping(
                value.get("recovery") if isinstance(value.get("recovery"), Mapping) else None
            ),
            gates=tuple(
                gate
                for gate in (RuntimeGateView.from_mapping(item) for item in _mapping_items(value.get("gates")))
                if gate.gate_id
            ),
            evidence=tuple(
                evidence
                for evidence in (
                    RuntimeEvidenceView.from_mapping(item) for item in _mapping_items(value.get("evidence"))
                )
                if evidence.evidence_id
            ),
            parity_gaps=tuple(RuntimeGapView.from_mapping(item) for item in _mapping_items(value.get("parity_gaps"))),
            semantic_deviations=tuple(
                RuntimeGapView.from_mapping(item) for item in _mapping_items(value.get("semantic_deviations"))
            ),
            updated_at=max(0.0, _as_float(value.get("updated_at"), time.time())),
            stale_after_seconds=max(1.0, _as_float(value.get("stale_after_seconds"), 60.0)),
            source_sequence=max(0, _as_int(value.get("source_sequence"))),
            explicitly_degraded=bool(value.get("degraded", False)),
        )

    def validated_copy(self) -> "WorkflowRuntimeOperationRecord":
        """Rebuild the complete record through its canonical loading boundary.

        Dataclass construction is intentionally convenient for internal
        producers, but Python does not enforce its annotations. Re-entering
        through ``from_mapping`` validates nested gate/evidence bindings as
        well as the tenant, run, workflow, and task identities before storage.
        """

        payload = asdict(self)
        payload["degraded"] = payload.pop("explicitly_degraded")
        return type(self).from_mapping(payload)

    @property
    def verified_evidence(self) -> tuple[RuntimeEvidenceView, ...]:
        return tuple(item for item in self.evidence if item.verified)

    @property
    def completed_without_evidence(self) -> bool:
        return self.status in SUCCESS_STATES and not self.verified_evidence

    @property
    def degraded(self) -> bool:
        return bool(
            self.explicitly_degraded
            or self.fallbacks
            or self.parity_gaps
            or self.semantic_deviations
            or self.completed_without_evidence
            or self.recovery.status in {"failed", "blocked", "degraded"}
        )

    def is_stale(self, *, now: float | None = None) -> bool:
        return float(now if now is not None else time.time()) - self.updated_at > self.stale_after_seconds

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        stale = self.is_stale(now=now)
        reasons: list[str] = []
        if self.explicitly_degraded:
            reasons.append("runtime_marked_degraded")
        if self.fallbacks:
            reasons.append("fallback_observed")
        if self.parity_gaps:
            reasons.append("native_parity_gap")
        if self.semantic_deviations:
            reasons.append("semantic_deviation")
        if self.completed_without_evidence:
            reasons.append("success_without_verified_evidence")
        if stale:
            reasons.append("read_model_stale")
        outcome_claim = "unverified" if self.completed_without_evidence else self.status
        payload = {
            "schema": RUNTIME_OPERATIONS_RECORD_SCHEMA,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id or None,
            "task_id": self.task_id or None,
            "runtime": self.runtime,
            "mode": self.mode,
            "status": self.status,
            "outcome_claim": outcome_claim,
            "capabilities": [item.to_dict() for item in self.capabilities],
            "fallbacks": [item.to_dict() for item in self.fallbacks],
            "cost_micros": self.cost_micros,
            "latency_ms": self.latency_ms,
            "recovery": self.recovery.to_dict(),
            "gates": [item.to_dict() for item in self.gates],
            "evidence": [item.to_dict() for item in self.evidence],
            "parity_gaps": [item.to_dict() for item in self.parity_gaps],
            "semantic_deviations": [item.to_dict() for item in self.semantic_deviations],
            "open_gate_count": sum(1 for item in self.gates if item.status == "open"),
            "verified_evidence_count": len(self.verified_evidence),
            "degraded": self.degraded,
            "degraded_reasons": reasons,
            "stale": stale,
            "updated_at": self.updated_at,
            "stale_after_seconds": self.stale_after_seconds,
            "source_sequence": self.source_sequence,
        }
        # Defense in depth: no tenant identifier or secret-like key is exposed
        # through the UI projection.
        return dict(redact_json(payload))


__all__ = [
    "RUNTIME_OPERATIONS_RECORD_SCHEMA",
    "SUCCESS_STATES",
    "TERMINAL_STATES",
    "RuntimeCapabilityView",
    "RuntimeEvidenceView",
    "RuntimeFallbackView",
    "RuntimeGapView",
    "RuntimeGateView",
    "RuntimeRecoveryView",
    "WorkflowRuntimeOperationRecord",
]
