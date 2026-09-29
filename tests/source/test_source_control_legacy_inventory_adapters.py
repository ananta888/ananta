from __future__ import annotations

import inspect

from sqlmodel import SQLModel, Session, create_engine

from agent.services.source_control_legacy_inventory_adapters import (
    ComposedLegacySourceInventoryAdapter,
    SQLLegacyKnowledgePolicyReader,
    SourceRegistrySnapshotReader,
)
from agent.services.source_control_legacy_migration import (
    LegacyMigrationInventory,
)


class _EmptyReadPort:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith(("list", "read", "get", "load")):
            def read(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                if name.startswith("get"):
                    return None
                return ()

            return read
        raise AttributeError(name)


def _construct(adapter_type, resolver):
    positional = []
    keyword = {}
    for parameter in inspect.signature(adapter_type).parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        value = resolver(parameter.name)
        if value is None:
            if parameter.default is not inspect.Parameter.empty:
                continue
            raise AssertionError(
                f"unsupported required adapter port: {parameter.name}"
            )
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            positional.append(value)
        else:
            keyword[parameter.name] = value
    return adapter_type(*positional, **keyword)


def _read_inventory(adapter) -> LegacyMigrationInventory:
    result = adapter.load_inventory(
        tenant_id="tenant-example",
        project_id="project-example",
        owner_id="owner-example",
    )
    assert isinstance(result, LegacyMigrationInventory)
    return result


def test_concrete_readers_compose_empty_inventory_without_mutation(
    tmp_path,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy-inventory.sqlite3'}"
    )
    SQLModel.metadata.create_all(engine)
    registry = _EmptyReadPort()
    snapshots = _EmptyReadPort()
    citations = _EmptyReadPort()

    source_reader = _construct(
        SourceRegistrySnapshotReader,
        lambda name: (
            registry
            if "registry" in name
            else snapshots
            if "snapshot" in name or "store" in name
            else None
        ),
    )
    knowledge_reader = _construct(
        SQLLegacyKnowledgePolicyReader,
        lambda name: (
            engine
            if "engine" in name
            else (lambda: Session(engine))
            if "session" in name
            else None
        ),
    )
    adapter = _construct(
        ComposedLegacySourceInventoryAdapter,
        lambda name: (
            source_reader
            if "source" in name
            else knowledge_reader
            if "knowledge" in name or "policy" in name
            else citations
            if "citation" in name
            else None
        ),
    )

    inventory = _read_inventory(adapter)

    assert inventory.source_snapshots == ()
    assert inventory.context_policies == ()
    assert inventory.knowledge_indexes == ()
    assert inventory.index_runs == ()
    assert inventory.citations == ()
    assert not any(
        call[0].startswith(("save", "create", "update", "delete"))
        for port in (registry, snapshots, citations)
        for call in port.calls
    )
