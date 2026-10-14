"""Scoped immutable assets, quarantine/CAS and strict provider compatibility."""

from types import SimpleNamespace

import pytest

from ananta_contracts.evolution_assets import EvolutionAsset
from plugins.evolver_adapter.mapper import EvolverResponseSchemaError, map_evolver_result


def asset():
    return EvolutionAsset(
        "strategy",
        "evolver",
        "1.94.0",
        "gene-fixture",
        {"type": "Gene", "id": "gene-fixture", "summary": "synthetic", "unknown": {"kept": True}},
        "legacy-pinned-v1.94.0",
    )


def test_content_address_and_provider_fields_are_preserved_without_evidence_authority():
    value = asset().to_mapping()
    assert EvolutionAsset.from_mapping(value).to_mapping() == value
    assert value["evidence_status"] == "unverified" and not value["grants_permission"]
    value["payload"]["summary"] = "tampered"
    with pytest.raises(ValueError, match="mutated"):
        EvolutionAsset.from_mapping(value)


def test_mapper_preserves_structured_assets_beside_legacy_proposals():
    row = {
        "kind": "Gene",
        "provider_asset_id": "gene-fixture",
        "provider_schema_version": "legacy-pinned-v1.94.0",
        "proposal_candidate": True,
        "raw_payload": dict(asset().payload),
    }
    result = map_evolver_result(
        {
            "status": "completed",
            "assets": [row],
            "proposals": [],
            "provider_metadata": {"runtime": {"version": "1.94.0"}, "output_mode": "structured_gep"},
        }
    )
    assert result.assets[0]["asset_type"] == "strategy" and result.assets[0]["payload"]["unknown"] == {"kept": True}
    assert result.provider_metadata["output_mode"] == "structured_gep"
    for change in ({"kind": "Unknown"}, {"provider_schema_version": "future"}):
        with pytest.raises(EvolverResponseSchemaError):
            map_evolver_result({"assets": [{**row, **change}]})


def test_asset_store_retains_history_and_never_activates_or_leaks_payload(tmp_path):
    from sqlmodel import Session, SQLModel, create_engine, select

    from agent.db_models.evolution_assets import EvolutionAssetDB, EvolutionAssetStateDB
    from agent.repositories.evolution_assets import EvolutionAssetStore

    db = create_engine("sqlite:///" + str(tmp_path / "assets.db"))
    SQLModel.metadata.create_all(db, tables=[EvolutionAssetDB.__table__, EvolutionAssetStateDB.__table__])
    store = EvolutionAssetStore(lambda: db)
    digest = store.append(tenant_id="tenant", project_id="project", asset=asset(), run_ref="analysis-fixture")
    assert store.append(tenant_id="tenant", project_id="project", asset=asset(), run_ref="analysis-fixture") == digest
    assert store.list(tenant_id="other", project_id="project") == []
    first = store.list(tenant_id="tenant", project_id="project")[0]
    assert "payload" not in first and first["state"] == "proposal"
    assert (
        store.transition(
            tenant_id="tenant",
            project_id="project",
            asset_digest=digest,
            expected_revision=0,
            state="quarantined",
            reason="automated test policy",
        )
        == 1
    )
    with pytest.raises(ValueError, match="revision_conflict"):
        store.transition(
            tenant_id="tenant",
            project_id="project",
            asset_digest=digest,
            expected_revision=0,
            state="disabled",
            reason="stale policy",
        )
    with pytest.raises(ValueError, match="transition_denied"):
        store.transition(
            tenant_id="tenant",
            project_id="project",
            asset_digest=digest,
            expected_revision=1,
            state="active",
            reason="model says yes",
        )
    with Session(db) as s:
        assert len(s.exec(select(EvolutionAssetStateDB)).all()) == 2
        assert s.exec(select(EvolutionAssetDB)).one().asset_json


def test_scope_or_mutated_asset_is_rejected_before_analysis_persistence():
    from agent.services.evolution.models import EvolutionContext, EvolutionPolicy, EvolutionResult, EvolutionTrigger
    from agent.services.evolution_run_service import EvolutionRunService

    class Runs:
        def save(self, run):
            raise AssertionError("invalid assets must not create a completed analysis")

    for task, snapshot in (
        (None, asset().to_mapping()),
        ({"tenant_id": "tenant", "project_id": "project"}, {**asset().to_mapping(), "asset_digest": "mutated"}),
    ):
        repositories = SimpleNamespace(evolution_run_repo=Runs(), task_repo=SimpleNamespace(get_by_id=lambda _: task))
        service = EvolutionRunService(repositories=repositories)
        with pytest.raises(ValueError):
            service.persist_analysis(
                EvolutionContext(objective="synthetic", task_id="task-fixture"),
                EvolutionResult(provider_name="evolver", assets=[snapshot]),
                EvolutionTrigger(trigger_type="manual", source="test"),
                policy=EvolutionPolicy(),
            )


def test_asset_migration_round_trip(monkeypatch):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    migration = importlib.import_module("migrations.versions.f1b3d5e7a9c2_add_evolution_assets")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert set(inspect(connection).get_table_names()) == {"evolution_assets", "evolution_asset_states"}
        migration.downgrade()
        assert inspect(connection).get_table_names() == []
