"""Hub-owned, signed comparison evidence for workflow-runtime shadow runs.

Observations are derived from the canonical Hub event store.  They deliberately
contain contracts and invariant outcomes, never raw model text or executable
runtime state.  A comparison is promotion evidence only; it is never a runtime
execution result.
"""

from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.events import CanonicalWorkflowEvent, EventStore
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime.security import HmacKeyRing

WORKFLOW_SHADOW_OBSERVATION_SCHEMA = "ananta.workflow_shadow_observation.v2"
WORKFLOW_SHADOW_COMPARISON_SCHEMA = "ananta.workflow_shadow_comparison.v2"
_TERMINAL_EVENTS = {
    "workflow.run.completed": "completed",
    "workflow.run.failed": "failed",
    "workflow.run.cancelled": "cancelled",
}
_MAX_EVIDENCE_BYTES = 1_048_576


@dataclass(frozen=True)
class WorkflowShadowRuntimeIdentity:
    """Immutable runtime identity supplied by the Hub runtime registry."""

    runtime_id: str
    runtime_version: str
    runtime_build: str
    capabilities: tuple[str, ...]

    def assert_valid(self) -> None:
        if not self.runtime_id or not self.runtime_version or not self.runtime_build:
            raise ValueError("workflow_shadow_runtime_identity_required")
        if not self.capabilities or any(not value for value in self.capabilities):
            raise ValueError("workflow_shadow_runtime_capabilities_required")


@dataclass(frozen=True)
class WorkflowShadowObservation:
    runtime_id: str
    runtime_version: str
    runtime_build: str
    tenant_id: str
    workflow_id: str
    run_id: str
    plan_hash: str
    terminal_status: str
    capabilities: tuple[str, ...]
    event_types: tuple[str, ...]
    artifact_contracts: tuple[tuple[str, str], ...]
    invariants: tuple[tuple[str, bool], ...]
    schema: str = WORKFLOW_SHADOW_OBSERVATION_SCHEMA

    @classmethod
    def build(
        cls,
        *,
        runtime_id: str,
        runtime_version: str,
        runtime_build: str,
        tenant_id: str,
        workflow_id: str,
        run_id: str,
        plan_hash: str,
        terminal_status: str,
        capabilities: tuple[str, ...] | list[str] | set[str] | frozenset[str],
        event_types: tuple[str, ...] | list[str],
        artifact_contracts: Mapping[str, str],
        invariants: Mapping[str, bool],
    ) -> WorkflowShadowObservation:
        if any(type(value) is not bool for value in invariants.values()):
            raise ValueError("workflow_shadow_invariant_boolean_required")
        observation = cls(
            runtime_id=str(runtime_id).strip(),
            runtime_version=str(runtime_version).strip(),
            runtime_build=str(runtime_build).strip(),
            tenant_id=str(tenant_id).strip(),
            workflow_id=str(workflow_id).strip(),
            run_id=str(run_id).strip(),
            plan_hash=str(plan_hash).strip(),
            terminal_status=str(terminal_status).strip().lower(),
            capabilities=tuple(sorted({str(value).strip() for value in capabilities if str(value).strip()})),
            event_types=tuple(str(value).strip() for value in event_types if str(value).strip()),
            artifact_contracts=tuple(
                sorted((str(key).strip(), str(value).strip()) for key, value in artifact_contracts.items())
            ),
            invariants=tuple(sorted((str(key).strip(), value) for key, value in invariants.items())),
        )
        observation.assert_valid()
        return observation

    def assert_valid(self) -> None:
        if self.schema != WORKFLOW_SHADOW_OBSERVATION_SCHEMA:
            raise ValueError("workflow_shadow_observation_schema_unsupported")
        if not all(
            (
                self.runtime_id,
                self.runtime_version,
                self.runtime_build,
                self.tenant_id,
                self.workflow_id,
                self.run_id,
                self.plan_hash,
                self.terminal_status,
            )
        ):
            raise ValueError("workflow_shadow_observation_binding_required")
        if self.terminal_status != "completed":
            raise RuntimeError("workflow_shadow_observation_not_successful")
        if not self.capabilities or not self.event_types or not self.invariants:
            raise ValueError("workflow_shadow_observation_evidence_empty")
        if any(not key or not value for key, value in self.artifact_contracts):
            raise ValueError("workflow_shadow_artifact_contract_invalid")
        if any(not key or type(value) is not bool for key, value in self.invariants):
            raise ValueError("workflow_shadow_invariant_invalid")
        if not all(value for _key, value in self.invariants):
            raise RuntimeError("workflow_shadow_observation_invariant_failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "runtime_id": self.runtime_id,
            "runtime_version": self.runtime_version,
            "runtime_build": self.runtime_build,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "plan_hash": self.plan_hash,
            "terminal_status": self.terminal_status,
            "capabilities": list(self.capabilities),
            "event_types": list(self.event_types),
            "artifact_contracts": dict(self.artifact_contracts),
            "invariants": dict(self.invariants),
        }


@dataclass(frozen=True)
class WorkflowShadowComparison:
    scope_key: str
    tenant_id: str
    workflow_id: str
    baseline_run_id: str
    shadow_run_id: str
    baseline_runtime: str
    baseline_runtime_version: str
    baseline_runtime_build: str
    shadow_runtime: str
    shadow_runtime_version: str
    shadow_runtime_build: str
    plan_hash: str
    policy_hash: str
    policy_version: str
    policy_revision: int
    required_capabilities: tuple[str, ...]
    common_capabilities: tuple[str, ...]
    status: str
    deviations: tuple[str, ...]
    source_revision: str
    issued_at: float
    expires_at: float
    key_id: str
    signature: str
    schema: str = WORKFLOW_SHADOW_COMPARISON_SCHEMA

    @property
    def evidence_ref(self) -> str:
        return "wsc-" + sha256_json(self._content())

    @property
    def promotion_safe(self) -> bool:
        return self.status == "passed" and not self.deviations

    def _content(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "scope_key": self.scope_key,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "baseline_run_id": self.baseline_run_id,
            "shadow_run_id": self.shadow_run_id,
            "baseline_runtime": self.baseline_runtime,
            "baseline_runtime_version": self.baseline_runtime_version,
            "baseline_runtime_build": self.baseline_runtime_build,
            "shadow_runtime": self.shadow_runtime,
            "shadow_runtime_version": self.shadow_runtime_version,
            "shadow_runtime_build": self.shadow_runtime_build,
            "plan_hash": self.plan_hash,
            "policy_hash": self.policy_hash,
            "policy_version": self.policy_version,
            "policy_revision": self.policy_revision,
            "required_capabilities": list(self.required_capabilities),
            "common_capabilities": list(self.common_capabilities),
            "status": self.status,
            "deviations": list(self.deviations),
            "source_revision": self.source_revision,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "key_id": self.key_id,
            "production_eligible": False,
        }

    def _signing_payload(self) -> dict[str, Any]:
        return {**self._content(), "evidence_ref": self.evidence_ref}

    def verify(
        self,
        *,
        key_ring: HmacKeyRing,
        now: float | None = None,
        scope_key: str = "",
        tenant_id: str = "",
        workflow_id: str = "",
        runtime_id: str = "",
        runtime_version: str = "",
        runtime_build: str = "",
        plan_hash: str = "",
        policy_hash: str = "",
        policy_version: str = "",
        policy_revision: int | None = None,
        source_revision: str = "",
    ) -> None:
        self.assert_promotion_safe()
        key_ring.verify(
            namespace=WORKFLOW_SHADOW_COMPARISON_SCHEMA,
            payload=self._signing_payload(),
            key_id=self.key_id,
            signature=self.signature,
            contract_id=self.evidence_ref,
        )
        timestamp = float(time.time() if now is None else now)
        if timestamp < self.issued_at or timestamp >= self.expires_at:
            raise ValueError("workflow_shadow_comparison_evidence_stale")
        expected = {
            "scope_key": scope_key,
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "shadow_runtime": runtime_id,
            "shadow_runtime_version": runtime_version,
            "shadow_runtime_build": runtime_build,
            "plan_hash": plan_hash,
            "policy_hash": policy_hash,
            "policy_version": policy_version,
            "source_revision": source_revision,
        }
        for field_name, value in expected.items():
            if value and getattr(self, field_name) != str(value):
                raise ValueError(f"workflow_shadow_comparison_{field_name}_mismatch")
        if policy_revision is not None and self.policy_revision != int(policy_revision):
            raise ValueError("workflow_shadow_comparison_policy_revision_mismatch")

    def assert_promotion_safe(self) -> None:
        required = (
            self.scope_key,
            self.tenant_id,
            self.workflow_id,
            self.baseline_run_id,
            self.shadow_run_id,
            self.baseline_runtime,
            self.baseline_runtime_version,
            self.baseline_runtime_build,
            self.shadow_runtime,
            self.shadow_runtime_version,
            self.shadow_runtime_build,
            self.plan_hash,
            self.policy_hash,
            self.policy_version,
            self.source_revision,
            self.key_id,
            self.signature,
        )
        if not all(required) or self.policy_revision < 1:
            raise ValueError("workflow_shadow_comparison_binding_required")
        if self.baseline_runtime == self.shadow_runtime or self.baseline_run_id == self.shadow_run_id:
            raise ValueError("workflow_shadow_comparison_distinct_runs_required")
        if self.issued_at <= 0 or self.expires_at <= self.issued_at:
            raise ValueError("workflow_shadow_comparison_freshness_invalid")
        if not self.required_capabilities or not self.common_capabilities:
            raise ValueError("workflow_shadow_comparison_capabilities_empty")
        if set(self.required_capabilities) - set(self.common_capabilities):
            raise RuntimeError("workflow_shadow_comparison_incompatible")
        if not self.promotion_safe:
            raise RuntimeError(f"workflow_shadow_comparison_{self.status}")

    def to_dict(self, *, include_evidence_ref: bool = True) -> dict[str, Any]:
        value = {**self._content(), "signature": self.signature}
        if include_evidence_ref:
            value["evidence_ref"] = self.evidence_ref
        return value


class WorkflowShadowComparisonService:
    """Compare deterministic contracts and sign the Hub-owned result."""

    def __init__(self, *, key_ring: HmacKeyRing, clock: Callable[[], float] = time.time) -> None:
        self._key_ring = key_ring
        self._clock = clock

    def compare(
        self,
        *,
        baseline: WorkflowShadowObservation,
        shadow: WorkflowShadowObservation,
        required_capabilities: tuple[str, ...] | frozenset[str] | set[str],
        source_revision: str,
        scope_key: str,
        policy_hash: str,
        policy_version: str,
        policy_revision: int,
        ttl_seconds: float = 3_600.0,
    ) -> WorkflowShadowComparison:
        baseline.assert_valid()
        shadow.assert_valid()
        required = tuple(sorted({str(value).strip() for value in required_capabilities if str(value).strip()}))
        if not required:
            raise ValueError("workflow_shadow_required_capabilities_empty")
        revision = str(source_revision).strip()
        normalized_scope = str(scope_key).strip()
        if not all((revision, normalized_scope, policy_hash, policy_version)) or int(policy_revision) < 1:
            raise ValueError("workflow_shadow_evidence_binding_required")
        if ttl_seconds <= 0:
            raise ValueError("workflow_shadow_evidence_ttl_invalid")
        common = tuple(sorted(set(baseline.capabilities) & set(shadow.capabilities)))
        missing = sorted(set(required) - set(common))
        deviations: list[str] = []
        for field_name in ("tenant_id", "workflow_id", "plan_hash"):
            if getattr(baseline, field_name) != getattr(shadow, field_name):
                deviations.append(f"{field_name}_drift")
        if baseline.terminal_status != shadow.terminal_status:
            deviations.append("terminal_status_drift")
        if baseline.event_types != shadow.event_types:
            deviations.append("event_invariant_drift")
        if baseline.artifact_contracts != shadow.artifact_contracts:
            deviations.append("artifact_contract_drift")
        if baseline.invariants != shadow.invariants:
            deviations.append("deterministic_invariant_drift")
        status = "incompatible" if missing else ("failed" if deviations else "passed")
        if missing:
            deviations.insert(0, "required_capability_missing:" + ",".join(missing))
        issued_at = float(self._clock())
        unsigned = WorkflowShadowComparison(
            scope_key=normalized_scope,
            tenant_id=baseline.tenant_id,
            workflow_id=baseline.workflow_id,
            baseline_run_id=baseline.run_id,
            shadow_run_id=shadow.run_id,
            baseline_runtime=baseline.runtime_id,
            baseline_runtime_version=baseline.runtime_version,
            baseline_runtime_build=baseline.runtime_build,
            shadow_runtime=shadow.runtime_id,
            shadow_runtime_version=shadow.runtime_version,
            shadow_runtime_build=shadow.runtime_build,
            plan_hash=baseline.plan_hash,
            policy_hash=str(policy_hash).strip(),
            policy_version=str(policy_version).strip(),
            policy_revision=int(policy_revision),
            required_capabilities=required,
            common_capabilities=common,
            status=status,
            deviations=tuple(deviations),
            source_revision=revision,
            issued_at=issued_at,
            expires_at=issued_at + float(ttl_seconds),
            key_id=self._key_ring.active_key_id,
            signature="",
        )
        key_id, signature = self._key_ring.sign(
            namespace=WORKFLOW_SHADOW_COMPARISON_SCHEMA,
            payload=unsigned._signing_payload(),
            key_id=unsigned.key_id,
        )
        return replace(unsigned, key_id=key_id, signature=signature)


class HubEventWorkflowShadowComparisonProducer:
    """Derive both observations exclusively from canonical Hub events."""

    def __init__(self, *, events: EventStore, comparison: WorkflowShadowComparisonService) -> None:
        self._events = events
        self._comparison = comparison

    def produce(
        self,
        *,
        plan: ExecutionPlan,
        scope_key: str,
        policy_hash: str,
        policy_version: str,
        policy_revision: int,
        baseline: WorkflowShadowRuntimeIdentity,
        baseline_run_id: str,
        shadow: WorkflowShadowRuntimeIdentity,
        shadow_run_id: str,
        source_revision: str,
        ttl_seconds: float = 3_600.0,
    ) -> WorkflowShadowComparison:
        plan.assert_valid()
        baseline.assert_valid()
        shadow.assert_valid()
        baseline_observation = self._observation(plan, baseline_run_id, baseline)
        shadow_observation = self._observation(plan, shadow_run_id, shadow)
        return self._comparison.compare(
            baseline=baseline_observation,
            shadow=shadow_observation,
            required_capabilities=tuple(sorted(set(plan.capabilities) | {"audit", "side_effect_guard"})),
            source_revision=source_revision,
            scope_key=scope_key,
            policy_hash=policy_hash,
            policy_version=policy_version,
            policy_revision=policy_revision,
            ttl_seconds=ttl_seconds,
        )

    def _observation(
        self,
        plan: ExecutionPlan,
        run_id: str,
        runtime: WorkflowShadowRuntimeIdentity,
    ) -> WorkflowShadowObservation:
        normalized_run_id = str(run_id).strip()
        if not normalized_run_id:
            raise ValueError("workflow_shadow_run_id_required")
        events = self._events.list_events(tenant_id=plan.tenant_id, run_id=normalized_run_id)
        invariants, terminal_status, artifacts = _derive_event_invariants(plan, normalized_run_id, events)
        return WorkflowShadowObservation.build(
            runtime_id=runtime.runtime_id,
            runtime_version=runtime.runtime_version,
            runtime_build=runtime.runtime_build,
            tenant_id=plan.tenant_id,
            workflow_id=plan.workflow_id,
            run_id=normalized_run_id,
            plan_hash=plan.plan_hash,
            terminal_status=terminal_status,
            capabilities=runtime.capabilities,
            event_types=tuple(event.event_type for event in events),
            artifact_contracts=artifacts,
            invariants=invariants,
        )


class WorkflowShadowComparisonEvidencePort(Protocol):
    def get_evidence(self, **bindings: Any) -> WorkflowShadowComparison: ...


class JsonWorkflowShadowComparisonEvidenceStore:
    """Verify one owner-only, signed comparison produced by the Hub."""

    def __init__(
        self,
        path: str | Path,
        *,
        key_ring: HmacKeyRing,
        expected_source_revision: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._key_ring = key_ring
        self._expected_source_revision = str(expected_source_revision).strip()
        self._clock = clock
        if not self._expected_source_revision:
            raise ValueError("workflow_shadow_expected_revision_required")

    def get_evidence(
        self,
        *,
        scope_key: str,
        tenant_id: str,
        workflow_id: str,
        runtime_id: str,
        runtime_version: str,
        runtime_build: str,
        plan_hash: str,
        policy_hash: str,
        policy_version: str,
        policy_revision: int,
    ) -> WorkflowShadowComparison:
        raw = _read_owner_only_json(self._path)
        comparison = _comparison_from_mapping(raw)
        if str(raw.get("evidence_ref") or "") != comparison.evidence_ref:
            raise ValueError("workflow_shadow_comparison_evidence_tampered")
        comparison.verify(
            key_ring=self._key_ring,
            now=self._clock(),
            scope_key=scope_key,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            runtime_id=runtime_id,
            runtime_version=runtime_version,
            runtime_build=runtime_build,
            plan_hash=plan_hash,
            policy_hash=policy_hash,
            policy_version=policy_version,
            policy_revision=policy_revision,
            source_revision=self._expected_source_revision,
        )
        return comparison


def _derive_event_invariants(
    plan: ExecutionPlan,
    run_id: str,
    events: tuple[CanonicalWorkflowEvent, ...],
) -> tuple[dict[str, bool], str, dict[str, str]]:
    if not events:
        raise ValueError("workflow_shadow_event_sequence_empty")
    if any(
        event.tenant_id != plan.tenant_id
        or event.workflow_id != plan.workflow_id
        or event.run_id != run_id
        for event in events
    ):
        raise ValueError("workflow_shadow_event_binding_mismatch")
    contiguous = tuple(event.sequence for event in events) == tuple(range(1, len(events) + 1))
    terminal_status = _TERMINAL_EVENTS.get(events[-1].event_type, "")
    start_events = [event for event in events if event.event_type == "workflow.run.started"]
    plan_bound = bool(start_events) and all(
        str(event.payload.get("plan_hash") or "") == plan.plan_hash for event in start_events
    )
    observed_nodes = {
        str(event.step_id or event.payload.get("node_id") or "").strip()
        for event in events
        if str(event.step_id or event.payload.get("node_id") or "").strip()
    }
    expected_nodes = {node.node_id for node in plan.nodes}
    artifacts: dict[str, str] = {}
    for event in events:
        artifact_id = str(event.payload.get("artifact_id") or "").strip()
        if artifact_id:
            artifacts[artifact_id] = str(
                event.payload.get("schema_ref") or event.payload.get("media_type") or "untyped"
            ).strip()
    required_artifacts = {artifact.artifact_id for artifact in plan.artifacts if artifact.required}
    invariants = {
        "event_sequence_contiguous": contiguous,
        "plan_hash_bound_by_start_event": plan_bound,
        "planned_nodes_observed": expected_nodes.issubset(observed_nodes),
        "required_artifacts_present": required_artifacts.issubset(artifacts),
        "terminal_success": terminal_status == "completed",
    }
    if not all(invariants.values()):
        failed = ",".join(sorted(key for key, value in invariants.items() if not value))
        raise RuntimeError("workflow_shadow_event_invariant_failed:" + failed)
    return invariants, terminal_status, artifacts


def _read_owner_only_json(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("workflow_shadow_comparison_evidence_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("workflow_shadow_comparison_evidence_not_regular")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError("workflow_shadow_comparison_evidence_not_owner_only")
        if metadata.st_size < 1 or metadata.st_size > _MAX_EVIDENCE_BYTES:
            raise ValueError("workflow_shadow_comparison_evidence_size_invalid")
        payload = os.read(descriptor, _MAX_EVIDENCE_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("workflow_shadow_comparison_evidence_invalid") from exc
    if not isinstance(raw, dict) or raw.get("schema") != WORKFLOW_SHADOW_COMPARISON_SCHEMA:
        raise ValueError("workflow_shadow_comparison_schema_unsupported")
    return raw


def _comparison_from_mapping(raw: Mapping[str, Any]) -> WorkflowShadowComparison:
    list_fields = ("required_capabilities", "common_capabilities", "deviations")
    if raw.get("production_eligible") is not False or any(
        not isinstance(raw.get(field), list)
        or not all(isinstance(value, str) and value for value in raw[field])
        for field in list_fields
    ):
        raise ValueError("workflow_shadow_comparison_evidence_invalid")
    try:
        return WorkflowShadowComparison(
            scope_key=str(raw.get("scope_key") or ""),
            tenant_id=str(raw.get("tenant_id") or ""),
            workflow_id=str(raw.get("workflow_id") or ""),
            baseline_run_id=str(raw.get("baseline_run_id") or ""),
            shadow_run_id=str(raw.get("shadow_run_id") or ""),
            baseline_runtime=str(raw.get("baseline_runtime") or ""),
            baseline_runtime_version=str(raw.get("baseline_runtime_version") or ""),
            baseline_runtime_build=str(raw.get("baseline_runtime_build") or ""),
            shadow_runtime=str(raw.get("shadow_runtime") or ""),
            shadow_runtime_version=str(raw.get("shadow_runtime_version") or ""),
            shadow_runtime_build=str(raw.get("shadow_runtime_build") or ""),
            plan_hash=str(raw.get("plan_hash") or ""),
            policy_hash=str(raw.get("policy_hash") or ""),
            policy_version=str(raw.get("policy_version") or ""),
            policy_revision=int(raw.get("policy_revision")),
            required_capabilities=tuple(raw.get("required_capabilities") or ()),
            common_capabilities=tuple(raw.get("common_capabilities") or ()),
            status=str(raw.get("status") or ""),
            deviations=tuple(raw.get("deviations") or ()),
            source_revision=str(raw.get("source_revision") or ""),
            issued_at=float(raw.get("issued_at")),
            expires_at=float(raw.get("expires_at")),
            key_id=str(raw.get("key_id") or ""),
            signature=str(raw.get("signature") or ""),
            schema=str(raw.get("schema") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow_shadow_comparison_evidence_invalid") from exc


__all__ = [
    "WORKFLOW_SHADOW_COMPARISON_SCHEMA",
    "WORKFLOW_SHADOW_OBSERVATION_SCHEMA",
    "HubEventWorkflowShadowComparisonProducer",
    "JsonWorkflowShadowComparisonEvidenceStore",
    "WorkflowShadowComparison",
    "WorkflowShadowComparisonEvidencePort",
    "WorkflowShadowComparisonService",
    "WorkflowShadowObservation",
    "WorkflowShadowRuntimeIdentity",
]
