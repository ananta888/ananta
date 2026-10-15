"""Append-only route bindings with a read adapter for legacy genome metadata."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.harness_evolution import HarnessGenomeDB
from agent.db_models.harness_routing import HarnessRouteBindingDB
from ananta_contracts.harness_genome import HarnessGenome, canonical_json, content_digest
from ananta_contracts.harness_routing import HarnessRouteEntry


def _engine():
    from agent.database import engine

    return engine


class SqlHarnessRoutingCatalog:
    def __init__(self, engine: Callable = _engine):
        self._engine = engine

    def append(self, entry: HarnessRouteEntry):
        """Persist references only; routing still verifies evidence before use."""
        entry.genome.assert_valid()
        if any(
            not isinstance(v, str) or not v.strip() or len(v) > 200
            for v in (entry.tenant_id, entry.project_id, entry.model_revision)
        ):
            raise ValueError("harness_route_binding_scope_invalid")
        binding = self.requirements(entry)["certified_route"]
        encoded = canonical_json(binding)
        if len(encoded.encode()) > 32768:
            raise ValueError("harness_route_binding_budget")
        digest = content_digest({"genome_id": entry.genome.genome_id, "binding": binding})
        with Session(self._engine()) as session:
            genome = session.get(HarnessGenomeDB, entry.genome.genome_id)
            if genome is None or genome.canonical_json != entry.genome.to_json():
                raise ValueError("harness_route_genome_unknown_or_mismatched")
            session.add(
                HarnessRouteBindingDB(
                    binding_digest=digest,
                    genome_id=genome.genome_id,
                    tenant_id=entry.tenant_id,
                    project_id=entry.project_id,
                    binding_json=encoded,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.get(HarnessRouteBindingDB, digest)
                if existing is None or existing.binding_json != encoded:
                    raise ValueError("harness_route_binding_conflict")
        return digest

    def entries(self, *, tenant_id: str, project_id: str):
        with Session(self._engine()) as session:
            bindings = session.exec(
                select(HarnessRouteBindingDB, HarnessGenomeDB)
                .join(HarnessGenomeDB, HarnessGenomeDB.genome_id == HarnessRouteBindingDB.genome_id)
                .where(HarnessRouteBindingDB.tenant_id == tenant_id, HarnessRouteBindingDB.project_id == project_id)
                .order_by(HarnessRouteBindingDB.binding_digest)
                .limit(257)
            ).all()
            result = [self._entry(genome, json.loads(binding.binding_json)) for binding, genome in bindings]
            seen = {(entry.genome.genome_id, canonical_json(self.requirements(entry))) for entry in result}
            rows = session.exec(
                select(HarnessGenomeDB)
                .where(
                    HarnessGenomeDB.model_requirements["tenant_id"].as_string() == tenant_id,
                    HarnessGenomeDB.model_requirements["project_id"].as_string() == project_id,
                )
                .limit(257)
            ).all()
            for row in rows:
                binding = row.model_requirements.get("certified_route")
                if not isinstance(binding, dict):
                    continue
                entry = self._entry(row, binding)
                key = (entry.genome.genome_id, canonical_json(self.requirements(entry)))
                if key not in seen:
                    result.append(entry)
                    seen.add(key)
            return result

    @staticmethod
    def _entry(genome, binding):
        values = dict(binding)
        for key in ("task_classes", "hardware_profiles", "certificate_source_ids"):
            values[key] = tuple(values[key])
        return HarnessRouteEntry(genome=HarnessGenome.from_mapping(json.loads(genome.canonical_json)), **values)

    @staticmethod
    def requirements(entry: HarnessRouteEntry):
        return json.loads(
            canonical_json(
                {
                    "tenant_id": entry.tenant_id,
                    "project_id": entry.project_id,
                    "certified_route": {f.name: getattr(entry, f.name) for f in fields(entry) if f.name != "genome"},
                }
            )
        )
