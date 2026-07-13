"""Focused Hub composition for production workflow-runtime selection."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from agent.common.audit import log_audit
from agent.services.workflow_runtime_selection_service import (
    ExplicitFallbackPolicy,
    InMemoryRuntimeCatalog,
    RuntimeCandidate,
    RuntimeCatalogPort,
    RuntimeHealthPort,
    RuntimeHealthSnapshot,
    RuntimeReleaseEvidencePort,
    RuntimeSelectionAuditPort,
    RuntimeSelectionAuditRecord,
    RuntimeSelectionProfile,
    StrictRuntimeBudgetService,
    StrictRuntimeDataLocalityService,
    VersionBoundRuntimePolicy,
    WorkflowRuntimeSelectionService,
)

PROTECTED_COMPATIBILITY_CAPABILITIES = frozenset(
    {"audit", "authorization", "policy", "side_effect_guard"}
)


class ConfiguredRuntimeHealth:
    """Development/test health for an infrastructure adapter in this process."""

    def __init__(self, runtime_id: str) -> None:
        self._runtime_id = str(runtime_id)

    def get_health(self, runtime_id: str) -> RuntimeHealthSnapshot:
        if str(runtime_id) != self._runtime_id:
            return RuntimeHealthSnapshot(
                str(runtime_id),
                "unavailable",
                "runtime_health_not_configured",
            )
        return RuntimeHealthSnapshot(
            self._runtime_id,
            "ready",
            "runtime_health_configured_backend_ready",
        )


class LocalCompatibilityReleaseEvidence:
    """Allow only the non-executing local compatibility status capability set."""

    def evaluate(
        self,
        *,
        runtime_id: str,
        required_capabilities: frozenset[str],
        **_: Any,
    ) -> tuple[bool, str]:
        del required_capabilities
        # Capability eligibility is evaluated independently against the
        # catalog candidate.  This evidence attests only that the in-process
        # compatibility adapter itself is admitted; it must not turn an
        # ordinary capability mismatch into a release-safety failure.
        allowed = runtime_id == "ananta-native"
        return (
            (True, "runtime_release_local_compatibility_adapter")
            if allowed
            else (False, "runtime_release_local_execution_not_admitted")
        )


class UnavailableRuntimeReleaseEvidence:
    def evaluate(self, **_: Any) -> tuple[bool, str]:
        return False, "runtime_release_evidence_unavailable"


class HubRuntimeSelectionAudit:
    def record(self, record: RuntimeSelectionAuditRecord) -> None:
        log_audit("workflow_runtime_selection", record.to_dict())


class ProtectedConfiguredRuntimeSelection:
    """Require Hub governance capabilities for every compatibility selection."""

    def __init__(self, selection: WorkflowRuntimeSelectionService) -> None:
        self._selection = selection

    def select(
        self,
        *,
        plan: Any,
        preferred_runtime: str,
        allowed_runtimes: tuple[str, ...],
        profile: Any | None = None,
        context: Any | None = None,
    ) -> Any:
        if profile is None:
            preferred = str(preferred_runtime)
            allowed = tuple(allowed_runtimes or ((preferred,) if preferred else ()))
            resolved = RuntimeSelectionProfile(
                profile_id="configured-backend-protected",
                preferred_runtime=preferred,
                allowed_runtimes=allowed,
                required_capabilities=tuple(
                    sorted(PROTECTED_COMPATIBILITY_CAPABILITIES)
                ),
                explicit_fallback_policy=ExplicitFallbackPolicy(),
            )
        elif isinstance(profile, RuntimeSelectionProfile):
            resolved = RuntimeSelectionProfile(
                profile_id=profile.profile_id,
                preferred_runtime=profile.preferred_runtime,
                allowed_runtimes=profile.allowed_runtimes,
                required_capabilities=tuple(
                    sorted(
                        set(profile.required_capabilities)
                        | set(PROTECTED_COMPATIBILITY_CAPABILITIES)
                    )
                ),
                explicit_fallback_policy=profile.explicit_fallback_policy,
            )
        else:
            parsed = RuntimeSelectionProfile.from_mapping(profile)
            return self.select(
                plan=plan,
                preferred_runtime=preferred_runtime,
                allowed_runtimes=allowed_runtimes,
                profile=parsed,
                context=context,
            )
        return self._selection.select(
            plan=plan,
            preferred_runtime="",
            allowed_runtimes=(),
            profile=resolved,
            context=context,
        )


def configured_runtime_id(backend_id: str) -> str:
    return "ananta-native" if str(backend_id) == "local" else str(backend_id)


def build_configured_workflow_runtime_selection(
    backend: Any,
    *,
    health: RuntimeHealthPort | None = None,
    release_evidence: RuntimeReleaseEvidencePort | None = None,
    audit: RuntimeSelectionAuditPort | None = None,
    native_production: bool = False,
    registered_runtime_ids: tuple[str, ...] = (),
    capability_catalog: RuntimeCatalogPort | None = None,
) -> ProtectedConfiguredRuntimeSelection:
    """Build the common selector for the one backend this legacy bridge can use.

    The catalog is intentionally limited to that bridge.  Runtime choice still
    evaluates capabilities, policy version, locality, budgets, observed health
    and release evidence; the bridge cannot dispatch to another container by
    bypassing the Hub task system.
    """

    backend_id = str(backend.backend_id)
    runtime_id = configured_runtime_id(backend_id)
    registered = tuple(dict.fromkeys(registered_runtime_ids or (runtime_id,)))
    if capability_catalog is None:
        candidates = [
            _candidate(value, native_production=native_production)
            for value in registered
        ]
    else:
        by_id = {
            candidate.runtime_id: candidate
            for candidate in capability_catalog.list_candidates()
        }
        missing = set(registered) - set(by_id)
        if missing:
            raise ValueError(
                "registered_runtime_capability_missing:" + ",".join(sorted(missing))
            )
        source_build = str(
            os.getenv("ANANTA_SOURCE_REVISION") or "development-unverified"
        ).strip()
        candidates = [
            replace(
                by_id[value],
                build_id=by_id[value].build_id or source_build,
            )
            for value in registered
        ]
    evidence = release_evidence
    if evidence is None:
        if backend_id == "local":
            evidence = LocalCompatibilityReleaseEvidence()
        else:
            evidence = UnavailableRuntimeReleaseEvidence()
    return ProtectedConfiguredRuntimeSelection(
        WorkflowRuntimeSelectionService(
            catalog=InMemoryRuntimeCatalog(tuple(candidates)),
            health=health or ConfiguredRuntimeHealth(runtime_id),
            policy=VersionBoundRuntimePolicy(),
            locality=StrictRuntimeDataLocalityService(),
            budget=StrictRuntimeBudgetService(),
            audit=audit or HubRuntimeSelectionAudit(),
            release_evidence=evidence,
        )
    )


def _candidate(runtime_id: str, *, native_production: bool) -> RuntimeCandidate:
    build_id = str(os.getenv("ANANTA_SOURCE_REVISION") or "development-unverified").strip()
    if runtime_id == "temporal":
        return RuntimeCandidate(
            runtime_id="temporal",
            version="1.0.0",
            build_id=build_id,
            mode="durable",
            capabilities=frozenset(
                {
                    *PROTECTED_COMPATIBILITY_CAPABILITIES,
                    "approval",
                    "bounded_parallel",
                    "checkpoint",
                    "deterministic_merge",
                    "durability",
                    "resume",
                    "retrieval",
                    "structured_output",
                    "tool_calling",
                }
            ),
            data_localities=frozenset({"any", "eu", "local"}),
            policy_versions=frozenset({"*"}),
            max_timeout_seconds=2_592_000,
            max_tokens=1_000_000,
            max_cost_micros=100_000_000,
            priority=10,
        )
    if runtime_id == "ananta-native":
        capabilities = set(PROTECTED_COMPATIBILITY_CAPABILITIES)
        if native_production:
            capabilities.update(
                {
                    "approval",
                    "bounded_parallel",
                    "checkpoint",
                    "deterministic_merge",
                    "resume",
                    "retrieval",
                    "stream",
                    "structured_output",
                    "subgraphs",
                    "tool_calling",
                }
            )
        return RuntimeCandidate(
            runtime_id="ananta-native",
            version="1.0.0",
            build_id=build_id,
            mode="live",
            capabilities=frozenset(capabilities),
            data_localities=frozenset({"any", "eu", "local"}),
            policy_versions=frozenset({"*"}),
            max_timeout_seconds=3_600,
            max_tokens=1_000_000,
            max_cost_micros=100_000_000,
            priority=10,
        )
    if runtime_id == "langgraph":
        return RuntimeCandidate(
            runtime_id="langgraph",
            version="1.0.0",
            build_id=build_id,
            mode="live",
            capabilities=frozenset(
                {
                    *PROTECTED_COMPATIBILITY_CAPABILITIES,
                    "approval",
                    "bounded_parallel",
                    "checkpoint",
                    "deterministic_merge",
                    "retrieval",
                    "resume",
                    "structured_output",
                    "subgraphs",
                    "tool_calling",
                    "workflow.adapter.langgraph",
                }
            ),
            data_localities=frozenset({"any", "eu", "local"}),
            policy_versions=frozenset({"*"}),
            max_timeout_seconds=86_400,
            max_tokens=1_000_000,
            max_cost_micros=100_000_000,
            priority=20,
        )
    raise ValueError("configured_workflow_runtime_unknown")


__all__ = [
    "ConfiguredRuntimeHealth",
    "HubRuntimeSelectionAudit",
    "LocalCompatibilityReleaseEvidence",
    "PROTECTED_COMPATIBILITY_CAPABILITIES",
    "ProtectedConfiguredRuntimeSelection",
    "UnavailableRuntimeReleaseEvidence",
    "build_configured_workflow_runtime_selection",
    "configured_runtime_id",
]
