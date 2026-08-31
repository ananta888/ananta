"""Adapter from the scientific-skill pilot to controlling statistics."""

from __future__ import annotations

import hashlib
import json

from agent.services.business_controlling_statistics import (
    StatisticalCapabilityDecision,
)
from agent.services.scientific_skill_catalog_service import ScientificSkillCatalog
from agent.services.scientific_skill_pilot_service import ScientificSkillPilotService
from agent.services.source_control_access_policy import (
    HubSourcePrincipal,
    SourceObjectBinding,
)


class ScientificPilotStatisticalCapabilityGate:
    """Authorize only scoped, pinned, offline controlled-execution cards."""

    def __init__(
        self,
        *,
        pilot: ScientificSkillPilotService,
        catalog: ScientificSkillCatalog,
        principal: HubSourcePrincipal,
        binding: SourceObjectBinding,
    ) -> None:
        self._pilot = pilot
        self._catalog = catalog
        self._principal = principal
        self._binding = binding

    def assess(
        self,
        *,
        tenant_id: str,
        project_id: str,
        skill_name: str,
        catalog_entry_id: str,
    ) -> StatisticalCapabilityDecision:
        if (
            self._principal.tenant_id != tenant_id
            or self._principal.project_id != project_id
        ):
            return _denied(
                skill_name,
                catalog_entry_id,
                "controlling_statistical_scope_denied",
            )
        cards = self._pilot.available(
            catalog=self._catalog,
            principal=self._principal,
            binding=self._binding,
        )
        card = next(
            (
                item
                for item in cards
                if item.skill_name == skill_name
                and item.entry_id == catalog_entry_id
            ),
            None,
        )
        if card is None:
            return _denied(
                skill_name,
                catalog_entry_id,
                "controlling_statistical_capability_not_admitted",
            )
        local_execution = card.allowed_mode == "controlled-execution"
        network_allowed = card.network_profile != "denied"
        admitted = local_execution and not network_allowed
        material = {
            "entry_id": card.entry_id,
            "skill_name": card.skill_name,
            "upstream_pin": card.upstream_pin,
            "skill_sha256": card.skill_sha256,
            "allowed_mode": card.allowed_mode,
            "allowed_tools": card.allowed_tools,
            "network_profile": card.network_profile,
            "catalog_digest": self._catalog.catalog_digest,
        }
        return StatisticalCapabilityDecision(
            admitted=admitted,
            local_execution=local_execution,
            network_allowed=network_allowed,
            catalog_entry_id=card.entry_id,
            skill_name=card.skill_name,
            upstream_pin=card.upstream_pin,
            capability_digest=_digest(material),
            reason_code=(
                "controlling_statistical_capability_admitted"
                if admitted
                else "controlling_statistical_offline_controlled_execution_required"
            ),
        )


def _denied(
    skill_name: str,
    catalog_entry_id: str,
    reason_code: str,
) -> StatisticalCapabilityDecision:
    return StatisticalCapabilityDecision(
        admitted=False,
        local_execution=False,
        network_allowed=False,
        catalog_entry_id=catalog_entry_id,
        skill_name=skill_name,
        upstream_pin="",
        capability_digest="0" * 64,
        reason_code=reason_code,
    )


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["ScientificPilotStatisticalCapabilityGate"]
