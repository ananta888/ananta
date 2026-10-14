"""Provider assets have one Hub owner; external memory is a bounded reference only."""

from __future__ import annotations

import json
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models.evolution_assets import EvolutionAssetDB, EvolutionAssetStateDB
from ananta_contracts.evolution_assets import EvolutionAsset


def _engine():
    from agent.database import engine

    return engine


class EvolutionAssetStore:
    def __init__(self, engine: Callable = _engine):
        self._engine = engine

    @staticmethod
    def _scope(tenant_id, project_id):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in (tenant_id, project_id)):
            raise ValueError("evolution_asset_scope_required")
        return tenant_id, project_id

    def append(self, *, tenant_id, project_id, asset: EvolutionAsset, run_ref: str):
        self._scope(tenant_id, project_id)
        if not isinstance(run_ref, str) or not run_ref or len(run_ref) > 200:
            raise ValueError("evolution_asset_run_reference_required")
        snapshot = asset.to_mapping()
        key = (tenant_id, project_id, snapshot["asset_digest"])
        with Session(self._engine()) as s:
            existing = s.get(EvolutionAssetDB, key)
            if existing:
                if json.loads(existing.asset_json) != snapshot:
                    raise ValueError("evolution_asset_content_conflict")
                return snapshot["asset_digest"]
            s.add(
                EvolutionAssetDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    asset_digest=key[-1],
                    asset_json=json.dumps(snapshot, sort_keys=True, allow_nan=False),
                    run_ref=run_ref,
                )
            )
            s.add(
                EvolutionAssetStateDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    asset_digest=key[-1],
                    revision=0,
                    state="proposal",
                    reason="import_unverified",
                )
            )
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                winner = s.get(EvolutionAssetDB, key)
                if winner is None or json.loads(winner.asset_json) != snapshot:
                    raise ValueError("evolution_asset_content_conflict")
            return key[-1]

    def _state(self, s, key):
        return s.exec(
            select(EvolutionAssetStateDB)
            .where(
                EvolutionAssetStateDB.tenant_id == key[0],
                EvolutionAssetStateDB.project_id == key[1],
                EvolutionAssetStateDB.asset_digest == key[2],
            )
            .order_by(EvolutionAssetStateDB.revision.desc())
        ).first()

    def transition(self, *, tenant_id, project_id, asset_digest, expected_revision, state, reason):
        self._scope(tenant_id, project_id)
        if (
            type(expected_revision) is not int
            or expected_revision < 0
            or state not in {"disabled", "quarantined", "retired"}
            or not isinstance(reason, str)
            or not reason
            or len(reason) > 500
        ):
            raise ValueError("evolution_asset_transition_denied")
        key = (tenant_id, project_id, asset_digest)
        with Session(self._engine()) as s:
            if s.get(EvolutionAssetDB, key) is None:
                raise ValueError("evolution_asset_unknown")
            previous = self._state(s, key)
            if previous is None or previous.revision != expected_revision:
                raise ValueError("evolution_asset_revision_conflict")
            if previous.state == "retired":
                raise ValueError("evolution_asset_transition_denied")
            s.add(
                EvolutionAssetStateDB(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    asset_digest=asset_digest,
                    revision=expected_revision + 1,
                    state=state,
                    reason=reason,
                )
            )
            try:
                s.commit()
            except IntegrityError as exc:
                raise ValueError("evolution_asset_revision_conflict") from exc
            return expected_revision + 1

    def list(self, *, tenant_id, project_id, limit=100):
        self._scope(tenant_id, project_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("evolution_asset_list_limit")
        with Session(self._engine()) as s:
            rows = s.exec(
                select(EvolutionAssetDB)
                .where(EvolutionAssetDB.tenant_id == tenant_id, EvolutionAssetDB.project_id == project_id)
                .order_by(EvolutionAssetDB.asset_digest)
                .limit(limit)
            ).all()
            output = []
            for row in rows:
                asset = EvolutionAsset.from_mapping(json.loads(row.asset_json)).to_mapping()
                state = self._state(s, (tenant_id, project_id, row.asset_digest))
                if state is None:
                    raise ValueError("evolution_asset_state_missing")
                output.append(
                    {
                        key: asset[key]
                        for key in (
                            "asset_digest",
                            "asset_type",
                            "provider_name",
                            "provider_version",
                            "provider_asset_id",
                            "provider_schema_version",
                            "evidence_status",
                            "grants_permission",
                        )
                    }
                    | {"state": state.state, "revision": state.revision, "run_ref": row.run_ref}
                )
            return output
