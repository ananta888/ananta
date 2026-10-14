"""Content-addressed provider-neutral assets; provider references remain unverified."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping

from ananta_contracts.harness_genome import content_digest

ASSET_SCHEMA = "ananta.evolution-asset.v1"
ASSET_TYPES = frozenset({"strategy", "experience", "event", "prompt"})


@dataclass(frozen=True)
class EvolutionAsset:
    asset_type: str
    provider_name: str
    provider_version: str
    provider_asset_id: str
    payload: Mapping
    provider_schema_version: str
    proposal_candidate: bool = True

    def to_mapping(self):
        if (
            self.asset_type not in ASSET_TYPES
            or not isinstance(self.payload, Mapping)
            or type(self.proposal_candidate) is not bool
            or any(
                not isinstance(v, str) or not v.strip() or len(v) > 200
                for v in (
                    self.provider_name,
                    self.provider_version,
                    self.provider_asset_id,
                    self.provider_schema_version,
                )
            )
        ):
            raise ValueError("evolution_asset_identity_invalid")
        value = {
            "schema": ASSET_SCHEMA,
            "asset_type": self.asset_type,
            "provider_name": self.provider_name,
            "provider_version": self.provider_version,
            "provider_asset_id": self.provider_asset_id,
            "provider_schema_version": self.provider_schema_version,
            "payload": dict(self.payload),
            "proposal_candidate": self.proposal_candidate,
            "evidence_status": "unverified",
            "grants_permission": False,
        }
        encoded = json.dumps(value, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 32768:
            raise ValueError("evolution_asset_payload_budget")
        return {**value, "asset_digest": content_digest(value)}

    @classmethod
    def from_mapping(cls, row):
        allowed = {
            "schema",
            "asset_type",
            "provider_name",
            "provider_version",
            "provider_asset_id",
            "provider_schema_version",
            "payload",
            "proposal_candidate",
            "evidence_status",
            "grants_permission",
            "asset_digest",
        }
        if not isinstance(row, Mapping) or set(row) != allowed or row["schema"] != ASSET_SCHEMA:
            raise ValueError("evolution_asset_schema_invalid")
        asset = cls(
            **{
                key: row[key]
                for key in (
                    "asset_type",
                    "provider_name",
                    "provider_version",
                    "provider_asset_id",
                    "payload",
                    "provider_schema_version",
                    "proposal_candidate",
                )
            }
        )
        if asset.to_mapping() != dict(row):
            raise ValueError("evolution_asset_content_mutated")
        return asset
