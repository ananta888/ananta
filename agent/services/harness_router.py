"""Hub routing selects already certified genomes; it never promotes one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ananta_contracts.harness_genome import HarnessGenome, content_digest
from ananta_contracts.harness_routing import HarnessRouteEntry, HarnessRouteRequirements


class HarnessRoutingCatalog(Protocol):
    def entries(self, *, tenant_id: str, project_id: str) -> Sequence[HarnessRouteEntry]: ...


class HarnessRoutingEvidence(Protocol):
    def require_source_identity(self, **bindings): ...
    def verify_release_binding(self, **bindings): ...


class HarnessRoutingAudit(Protocol):
    def record(self, event: dict): ...


@dataclass(frozen=True)
class HarnessRoutingDecision:
    status: str
    reason: str
    genome: HarnessGenome | None = None
    rejected: tuple[tuple[str, str], ...] = ()

    def as_dict(self):
        return {
            "schema": "ananta.harness-routing-decision.v1",
            "status": self.status,
            "reason": self.reason,
            "genome_id": self.genome.genome_id if self.genome else None,
            "rejected": [{"genome_id": g, "reason": r} for g, r in self.rejected],
        }


class HarnessRouter:
    def __init__(
        self,
        *,
        catalog: HarnessRoutingCatalog,
        evidence: HarnessRoutingEvidence,
        audit: HarnessRoutingAudit,
        required_scope: str = "production",
    ):
        if required_scope not in {"local", "external", "production"}:
            raise ValueError("harness_route_release_scope_invalid")
        self._catalog, self._evidence, self._audit, self._scope = catalog, evidence, audit, required_scope

    def route(self, request: HarnessRouteRequirements, *, task_id: str) -> HarnessRoutingDecision:
        request.validate()
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
            raise ValueError("harness_route_task_required")
        rejected = []
        eligible = []
        try:
            entries = tuple(self._catalog.entries(tenant_id=request.tenant_id, project_id=request.project_id))
            if len(entries) > 256:
                raise ValueError("harness_route_catalog_budget")
            for entry in entries:
                exact = entry.matches(request)
                fallback = entry.matches(request, fallback=True)
                if not exact and not fallback:
                    rejected.append((entry.genome.genome_id, "model_task_or_resource_mismatch"))
                    continue
                try:
                    self._certified(entry)
                except ValueError:
                    rejected.append((entry.genome.genome_id, "certificate_unverified"))
                    continue
                eligible.append(
                    (
                        not exact,
                        entry.baseline,
                        entry.estimated_cost,
                        entry.estimated_latency_ms,
                        entry.genome.genome_id,
                        entry,
                    )
                )
            if eligible:
                entry = min(eligible, key=lambda item: item[:-1])[-1]
                decision = HarnessRoutingDecision(
                    "baseline" if entry.baseline else "selected",
                    "certified_baseline_fallback" if entry.baseline else "exact_certified_binding",
                    entry.genome,
                    tuple(rejected),
                )
            else:
                decision = HarnessRoutingDecision("blocked", "certified_baseline_unavailable", rejected=tuple(rejected))
            self._audit.record(
                {
                    "event": "harness_route",
                    "tenant_id": request.tenant_id,
                    "project_id": request.project_id,
                    "task_id": task_id,
                    "request_digest": content_digest(request.__dict__),
                    **decision.as_dict(),
                }
            )
            return decision
        except Exception:
            return HarnessRoutingDecision("blocked", "harness_route_dependencies_unavailable")

    def _certified(self, entry: HarnessRouteEntry):
        source = self._evidence.require_source_identity(
            tenant_id=entry.tenant_id,
            project_id=entry.project_id,
            source_id=entry.certificate_source_id,
            expected_binding_digest=entry.certificate_source_binding,
        )
        if source.content_digest != entry.manifest_digest or source.synthetic or source.evidence_scope == "test":
            raise ValueError("harness_route_manifest_unverified")
        if entry.certificate_source_id not in entry.certificate_source_ids:
            raise ValueError("harness_route_source_not_in_run")
        verified = self._evidence.verify_release_binding(
            tenant_id=entry.tenant_id,
            project_id=entry.project_id,
            run_id=entry.certificate_run_id,
            required_scope=self._scope,
            task_id=entry.certificate_task_id,
            repository_revision=entry.repository_revision,
            source_ids=entry.certificate_source_ids,
        )
        if not verified.verified:
            raise ValueError("harness_route_release_unverified")


def get_harness_router():
    from flask import current_app, has_app_context

    if not has_app_context() or current_app.config.get("ROLE") != "hub":
        raise ValueError("harness_router_hub_required")
    router = current_app.extensions.get("harness_router")
    if router is None:
        from agent.repositories.harness_routing import SqlHarnessRoutingCatalog
        from agent.services.hub_evidence_registry_service import get_hub_evidence_registry_service

        router = HarnessRouter(
            catalog=SqlHarnessRoutingCatalog(), evidence=get_hub_evidence_registry_service(), audit=HarnessRouteAudit()
        )
        current_app.extensions["harness_router"] = router
    return router


class HarnessRouteAudit:
    def record(self, event):
        from agent.common.audit import log_audit

        log_audit("harness_route", event)
