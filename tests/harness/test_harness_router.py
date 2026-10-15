"""Model/version-bound selection; certificates are test doubles, never release evidence."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent.services.harness_router import HarnessRouter
from agent.services.harness_runtime import baseline_genome
from ananta_contracts.harness_routing import HarnessRouteEntry, HarnessRouteRequirements


def entry(*, baseline=False, model_revision="v1", task_classes=("coding",), hardware=("cpu",), cost=0.1):
    genome = baseline_genome(
        code_hash="a" * 64,
        config_hash="b" * 64,
        provider_id="local",
        model_id="model",
        gate_refs=("gate:policy",),
        promotion_ref="gate:promotion",
        allowed_tools=("read",),
    )
    return HarnessRouteEntry(
        genome,
        "tenant",
        "project",
        model_revision,
        task_classes,
        hardware,
        cost,
        100,
        "a" * 40,
        "",
        "",
        "",
        "",
        (),
        baseline,
    )


REQUEST = HarnessRouteRequirements(
    "tenant", "project", "local", "model", "v1", "coding", "cpu", ("read",), 32768, 1, 500
)


class Audit:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def router(entries, monkeypatch, *, certify=True, audit=None):
    catalog = SimpleNamespace(entries=lambda **scope: tuple(entries))
    r = HarnessRouter(catalog=catalog, evidence=SimpleNamespace(), audit=audit or Audit())
    if certify:
        # Explicit automated policy fixture tests only the routing decision.
        monkeypatch.setattr(r, "_certified", lambda _: None)
    return r


def test_exact_binding_and_certified_baseline_fallback(monkeypatch):
    optimized = entry()
    base = entry(baseline=True, task_classes=("*",))
    audit = Audit()
    r = router([base, optimized], monkeypatch, audit=audit)
    assert r.route(REQUEST, task_id="task").status == "selected"
    result = r.route(replace(REQUEST, task_class="unknown"), task_id="task")
    assert result.status == "baseline" and result.reason == "certified_baseline_fallback"
    assert audit.events[-1]["task_id"] == "task"
    assert "genome" not in audit.events[-1]  # no secret refs/full genome in audit


@pytest.mark.parametrize(
    "patch",
    [
        {"model_revision": "v2"},
        {"model_id": "other"},
        {"provider_id": "other"},
        {"tenant_id": "other"},
        {"project_id": "other"},
        {"authorized_tools": ()},
        {"hardware_profile": "gpu"},
        {"maximum_cost": 0},
        {"maximum_latency_ms": 50},
        {"context_tokens": 65536},
    ],
)
def test_unknown_model_scope_or_resources_cannot_reuse_other_genome(monkeypatch, patch):
    result = router([entry(), entry(baseline=True, task_classes=("*",))], monkeypatch).route(
        replace(REQUEST, **patch), task_id="task"
    )
    assert result.status == "blocked" and result.genome is None


def test_unverified_certificate_and_audit_failure_block_selection(monkeypatch):
    assert router([entry()], monkeypatch, certify=False).route(REQUEST, task_id="task").status == "blocked"

    class FailedAudit:
        def record(self, event):
            raise OSError("test")

    assert router([entry()], monkeypatch, audit=FailedAudit()).route(REQUEST, task_id="task").status == "blocked"


def test_real_registry_cannot_promote_test_or_unknown_ids(monkeypatch):
    from tests.hub.test_hub_evidence_registry_service import service as registry_fixture

    r = HarnessRouter(
        catalog=SimpleNamespace(entries=lambda **kw: (entry(),)), evidence=registry_fixture.__wrapped__(), audit=Audit()
    )
    assert r.route(REQUEST, task_id="task").status == "blocked"


def test_catalog_metadata_is_scope_filtered_and_reconstructs_genome(tmp_path):
    from sqlmodel import SQLModel, create_engine

    from agent.db_models.harness_evolution import HarnessGenomeDB
    from agent.db_models.harness_routing import HarnessRouteBindingDB
    from agent.repositories.harness_evolution import HarnessEvolutionStore
    from agent.repositories.harness_routing import SqlHarnessRoutingCatalog

    db = create_engine("sqlite:///" + str(tmp_path / "catalog.db"))
    SQLModel.metadata.create_all(db, tables=[HarnessGenomeDB.__table__, HarnessRouteBindingDB.__table__])
    e = entry()
    c = SqlHarnessRoutingCatalog(lambda: db)
    HarnessEvolutionStore(engine=lambda: db).append_genome(e.genome, model_requirements=c.requirements(e))
    assert c.entries(tenant_id="tenant", project_id="project")[0].manifest_digest == e.manifest_digest
    assert c.entries(tenant_id="other", project_id="project") == []


def test_routes_attach_after_genome_creation_and_are_revision_and_scope_bound(tmp_path):
    from sqlmodel import SQLModel, create_engine

    from agent.db_models.harness_evolution import HarnessGenomeDB
    from agent.db_models.harness_routing import HarnessRouteBindingDB
    from agent.repositories.harness_evolution import HarnessEvolutionStore
    from agent.repositories.harness_routing import SqlHarnessRoutingCatalog

    db = create_engine("sqlite:///" + str(tmp_path / "routes.db"))
    SQLModel.metadata.create_all(db, tables=[HarnessGenomeDB.__table__, HarnessRouteBindingDB.__table__])
    store = HarnessEvolutionStore(engine=lambda: db)
    catalog = SqlHarnessRoutingCatalog(lambda: db)
    original = entry()
    with pytest.raises(ValueError, match="genome_unknown"):
        catalog.append(original)
    store.append_genome(original.genome)
    before = store.get_genome(original.genome.genome_id)
    digest = catalog.append(original)
    assert catalog.append(original) == digest
    assert catalog.append(replace(original, model_revision="v2")) != digest
    catalog.append(replace(original, tenant_id="other"))
    assert {e.model_revision for e in catalog.entries(tenant_id="tenant", project_id="project")} == {"v1", "v2"}
    assert len(catalog.entries(tenant_id="other", project_id="project")) == 1
    assert catalog.entries(tenant_id="tenant", project_id="other") == []
    assert store.get_genome(original.genome.genome_id) == before
    # A persisted binding is still only a reference, never certification authority.
    r = HarnessRouter(catalog=catalog, evidence=SimpleNamespace(), audit=Audit())
    assert r.route(REQUEST, task_id="task").status == "blocked"


def test_legacy_route_import_is_idempotent_and_deduplicates_new_binding(tmp_path):
    from sqlmodel import SQLModel, create_engine

    from agent.db_models.harness_evolution import HarnessGenomeDB
    from agent.db_models.harness_routing import HarnessRouteBindingDB
    from agent.repositories.harness_evolution import HarnessEvolutionStore
    from agent.repositories.harness_routing import SqlHarnessRoutingCatalog

    db = create_engine("sqlite:///" + str(tmp_path / "legacy.db"))
    SQLModel.metadata.create_all(db, tables=[HarnessGenomeDB.__table__, HarnessRouteBindingDB.__table__])
    store = HarnessEvolutionStore(engine=lambda: db)
    catalog = SqlHarnessRoutingCatalog(lambda: db)
    e = entry()
    requirements = catalog.requirements(e)
    # Exercise both the original tuple representation and JSON-list round trips.
    requirements["certified_route"]["task_classes"] = e.task_classes
    for _ in range(2):
        store.append_genome(e.genome, model_requirements=requirements)
    catalog.append(e)
    assert len(catalog.entries(tenant_id="tenant", project_id="project")) == 1


def test_route_binding_migration_round_trip(monkeypatch):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from sqlmodel import SQLModel, create_engine

    from agent.db_models.harness_evolution import HarnessGenomeDB

    migration = importlib.import_module("migrations.versions.a2c4e6f8b0d3_add_harness_route_bindings")
    db = create_engine("sqlite://")
    SQLModel.metadata.create_all(db, tables=[HarnessGenomeDB.__table__])
    with db.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert "harness_route_bindings" in inspect(connection).get_table_names()
        migration.downgrade()
        assert inspect(connection).get_table_names() == ["harness_genomes"]
