from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.services.scientific_skill_adapter_service import (
    DocumentationResearchSkillAdapter,
    ScientificSkillAdapterRequest,
    ScientificSkillAdapterService,
    ScientificSkillAdapterStatus,
)
from agent.services.scientific_skill_catalog_service import ScientificSkillCatalog
from agent.services.scientific_skill_pilot_service import (
    SCIENTIFIC_SKILL_PILOT_ROLE,
    ScientificSkillPilotAuditEvent,
    ScientificSkillPilotService,
)
from agent.services.source_control_access_policy import HubSourcePrincipal, SourceObjectBinding


class _CatalogResolver:
    def __init__(self, catalog: ScientificSkillCatalog) -> None:
        self.catalog = catalog

    def get(self, *, catalog_id: str, catalog_version: str) -> ScientificSkillCatalog | None:
        if (catalog_id, catalog_version) == (
            self.catalog.catalog_id,
            self.catalog.catalog_version,
        ):
            return self.catalog
        return None


class _Audit:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.events: list[ScientificSkillPilotAuditEvent] = []

    def emit(self, event: ScientificSkillPilotAuditEvent) -> bool:
        self.events.append(event)
        return self.available


def _catalog() -> ScientificSkillCatalog:
    path = Path(__file__).parents[2] / "config" / "scientific-skills-catalog.json"
    return ScientificSkillCatalog.from_mapping(json.loads(path.read_text(encoding="utf-8")))


def _principal(*roles: str) -> HubSourcePrincipal:
    return HubSourcePrincipal("user-1", "tenant-1", "project-1", frozenset(roles))


def _binding() -> SourceObjectBinding:
    return SourceObjectBinding("scientific-skills-pilot", "tenant-1", "project-1")


def _service(catalog: ScientificSkillCatalog, audit: _Audit) -> ScientificSkillPilotService:
    adapter = ScientificSkillAdapterService(
        _CatalogResolver(catalog),
        (DocumentationResearchSkillAdapter(),),
    )
    return ScientificSkillPilotService(adapter_service=adapter, audit_port=audit)


def _request(catalog: ScientificSkillCatalog, skill_name: str) -> ScientificSkillAdapterRequest:
    return ScientificSkillAdapterRequest(catalog.catalog_id, catalog.catalog_version, skill_name)


def test_pilot_is_invisible_without_flag_or_explicit_scoped_permission() -> None:
    disabled = _catalog()
    audit = _Audit()
    service = _service(disabled, audit)
    granted = _principal("project_owner", SCIENTIFIC_SKILL_PILOT_ROLE)
    assert service.available(catalog=disabled, principal=granted, binding=_binding()) == ()

    enabled = ScientificSkillCatalog.create(
        catalog_id=disabled.catalog_id,
        catalog_version=disabled.catalog_version,
        feature_enabled=True,
        entries=disabled.entries,
    )
    unauthorized = _principal("project_owner")
    assert service.available(catalog=enabled, principal=unauthorized, binding=_binding()) == ()
    foreign = SourceObjectBinding("scientific-skills-pilot", "tenant-2", "project-2")
    assert service.available(catalog=enabled, principal=granted, binding=foreign) == ()


def test_five_pinned_skills_project_context_source_and_audit_without_tool_execution() -> None:
    enabled = ScientificSkillCatalog.create(
        catalog_id=_catalog().catalog_id,
        catalog_version=_catalog().catalog_version,
        feature_enabled=True,
        entries=_catalog().entries,
    )
    audit = _Audit()
    service = _service(enabled, audit)
    principal = _principal("project_owner", SCIENTIFIC_SKILL_PILOT_ROLE)

    cards = service.available(catalog=enabled, principal=principal, binding=_binding())
    assert tuple(card.skill_name for card in cards) == (
        "astropy",
        "networkx",
        "scvi-tools",
        "torch-geometric",
        "umap-learn",
    )
    assert all(card.upstream_pin == "cc37669ed0f354619b1ae586e958609a87680718" for card in cards)
    assert all(card.allowed_mode == "documentation-only" for card in cards)
    assert all(card.allowed_tools == () and card.network_profile == "denied" for card in cards)
    assert all(card.context_budget_tokens > 0 and "/blob/" in card.source_reference for card in cards)

    outcome = service.select(
        catalog=enabled,
        principal=principal,
        binding=_binding(),
        request=_request(enabled, "astropy"),
        task_id="task-pilot-1",
    )
    assert outcome.status is ScientificSkillAdapterStatus.PROJECTED
    assert outcome.projection is not None
    assert "Do not execute upstream files" in outcome.projection.instruction
    assert outcome.projection.source_references == (cards[0].source_reference,)
    assert audit.events[-1].selection_status == "selected"
    assert audit.events[-1].entry_id == cards[0].entry_id


def test_unadmitted_skill_is_rejected_and_an_audit_outage_fails_closed() -> None:
    base = _catalog()
    enabled = ScientificSkillCatalog.create(
        catalog_id=base.catalog_id,
        catalog_version=base.catalog_version,
        feature_enabled=True,
        entries=base.entries,
    )
    principal = _principal("project_owner", SCIENTIFIC_SKILL_PILOT_ROLE)
    audit = _Audit()
    service = _service(enabled, audit)
    rejected = service.select(
        catalog=enabled,
        principal=principal,
        binding=_binding(),
        request=_request(enabled, "not-admitted"),
        task_id="task-pilot-2",
    )
    assert rejected.degradation_code == "scientific_skill_pilot_not_admitted"
    assert audit.events[-1].selection_status == "rejected"
    assert audit.events[-1].entry_id is None

    unavailable_audit = _Audit(available=False)
    unaudited = _service(enabled, unavailable_audit).select(
        catalog=enabled,
        principal=principal,
        binding=_binding(),
        request=_request(enabled, "networkx"),
        task_id="task-pilot-3",
    )
    assert unaudited.degradation_code == "scientific_skill_pilot_audit_required"


def test_review_receipts_reproduce_and_bind_every_catalog_entry() -> None:
    path = Path(__file__).parents[2] / "config" / "scientific-skills-pilot-review.json"
    review = json.loads(path.read_text(encoding="utf-8"))
    catalog = _catalog()
    entries = {entry.skill_name: entry for entry in catalog.entries}
    assert set(entries) == {item["skill_name"] for item in review["reviews"]}

    for item in review["reviews"]:
        receipt_digest = item["approval_receipt_digest"]
        payload = dict(item)
        payload.pop("approval_receipt_digest")
        reproduced = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        entry = entries[item["skill_name"]]
        assert reproduced == receipt_digest == entry.approval_receipt_digest
        assert item["upstream_pin"] == entry.upstream_pin
        assert item["skill_sha256"] == entry.skill_sha256
        assert item["risk_profile_digest"] == entry.risk_profile_digest
