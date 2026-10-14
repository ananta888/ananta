"""Scope-filtered certified routing metadata in the existing genome store."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Callable

from sqlmodel import Session, select

from agent.db_models.harness_evolution import HarnessGenomeDB
from ananta_contracts.harness_genome import HarnessGenome
from ananta_contracts.harness_routing import HarnessRouteEntry


def _engine():
    from agent.database import engine

    return engine


class SqlHarnessRoutingCatalog:
    def __init__(self, engine: Callable = _engine):
        self._engine = engine

    def entries(self, *, tenant_id: str, project_id: str):
        with Session(self._engine()) as session:
            rows = session.exec(
                select(HarnessGenomeDB)
                .where(
                    HarnessGenomeDB.model_requirements["tenant_id"].as_string() == tenant_id,
                    HarnessGenomeDB.model_requirements["project_id"].as_string() == project_id,
                )
                .limit(257)
            ).all()
            result = []
            for row in rows:
                binding = row.model_requirements.get("certified_route")
                if not isinstance(binding, dict):
                    continue
                values = dict(binding)
                for key in ("task_classes", "hardware_profiles", "certificate_source_ids"):
                    values[key] = tuple(values[key])
                result.append(
                    HarnessRouteEntry(genome=HarnessGenome.from_mapping(json.loads(row.canonical_json)), **values)
                )
            return result

    @staticmethod
    def requirements(entry: HarnessRouteEntry):
        return {
            "tenant_id": entry.tenant_id,
            "project_id": entry.project_id,
            "certified_route": {f.name: getattr(entry, f.name) for f in fields(entry) if f.name != "genome"},
        }
