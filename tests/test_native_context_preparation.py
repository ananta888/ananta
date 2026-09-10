"""Headless Hub preparation with actual Task/Bundle models and bounded ports."""

import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine

from agent.db_models import ContextBundleDB, TaskDB
from agent.services.native_context_preparation_composition import (
    HubNativeContextPreparer,
    HubNativeContextSnapshotStore,
)
from agent.services.native_context_preparation_service import NativeContextPreparationService
from agent.services.native_context_snapshot import validate_prepared_native_context
from agent.services.native_graph_task_queue_adapter import AnantaHubTaskQueueAdapter
from agent.services.task_context_bundle_access_service import TaskContextBundleAccessService
from agent.services.task_queue_service import TaskQueueService
from tests.test_native_graph_task_adapters import FakeQueue, FakeRepository, FakeTaskRuntime
from tests.test_pi_hub_budget_composition import composition
from tests.test_pi_native_node import task_command


def preparation_setup():
    _, context, _ = composition()
    base = task_command(context)
    command = replace(base, node=replace(base.node, metadata={
        "context_bundle_mode": "control_task", "context_policy_id": "policy-1",
        "context_destination_id": "destination-1",
    }))
    tasks, bundles = FakeRepository(), FakeRepository()
    parent = TaskDB(
        id=command.control_task_id, tenant_id=command.tenant_id, project_id="project-1", status="in_progress",
        organization_id="organization-1", unit_id="unit-1", team_id="team-1", role_slot_id="role-1",
        context_bundle_id="original-bundle",
    )
    original = ContextBundleDB(
        id="original-bundle", task_id=parent.id, context_text="unapproved assembled text",
        chunks=[{
            "engine": "codecompass_fts", "content": "def example(): return 1",
            "metadata": {"file": "docs/public/example.py", "sensitivity": "public", "ignored": "private"},
        }],
    )
    tasks.values[parent.id], bundles.values[original.id] = parent, original

    def save(bundle):
        assert bundle.id not in bundles.values, "never overwrite an existing snapshot"
        bundles.values[bundle.id] = bundle
        return bundle

    bundles.save = Mock(side_effect=save)
    service = NativeContextPreparationService(
        tasks=tasks, bundles=TaskContextBundleAccessService(bundles), snapshots=HubNativeContextSnapshotStore(bundles),
    )
    queue = FakeQueue(tasks)
    adapter = AnantaHubTaskQueueAdapter(
        task_queue=queue, task_repository=tasks, task_runtime=FakeTaskRuntime(tasks), context_preparer=service,
    )
    return SimpleNamespace(**locals())


def test_hub_automatically_copies_owned_bundle_and_complete_scope_without_rebinding():
    f = preparation_setup()
    before = copy.deepcopy(f.original.model_dump())
    receipt = f.adapter.submit(f.command)
    assert receipt.accepted and f.adapter.submit(f.command).accepted
    assert len(f.queue.ingested_values) == f.bundles.save.call_count == 1
    ingested = f.queue.ingested_values[0]
    fields = ingested["extra_fields"]
    assert {key: fields[key] for key in ("tenant_id", "project_id", "organization_id", "unit_id", "role_slot_id")} == {
        "tenant_id": "tenant-1", "project_id": "project-1", "organization_id": "organization-1",
        "unit_id": "unit-1", "role_slot_id": "role-1",
    }
    assert ingested["team_id"] == "team-1" and "team_id" not in fields
    assert fields["parent_task_id"] == f.parent.id
    assert "plan_id" not in fields and "plan_node_id" not in fields
    snapshot = f.bundles.get_by_id(fields["context_bundle_id"])
    assert snapshot.task_id == receipt.hub_task_id and snapshot.context_text is None
    assert f.original.model_dump() == before and snapshot.id != f.original.id
    assert snapshot.chunks == [{
        "source_ref": "docs/public/example.py", "source_type": "codecompass_code",
        "sensitivity": "public", "content": "def example(): return 1",
    }]
    assert "private" not in str(snapshot.model_dump())
    access = snapshot.bundle_metadata["native_context_access"]
    assert access == {
        "policy_id": "policy-1", "destination_id": "destination-1",
        "provider_endpoint_identity": f.command.provider_binding.endpoint_identity,
    }


def test_native_task_without_bundle_still_persists_the_explicit_hub_tenant():
    f = preparation_setup()
    command = replace(f.command, node=replace(f.command.node, metadata={}))
    receipt = f.adapter.submit(command)
    task = TaskDB(**vars(f.tasks.get_by_id(receipt.hub_task_id)))
    assert task.tenant_id == command.tenant_id
    assert task.project_id is None and task.parent_task_id is None and task.plan_id is None


def test_actual_queue_ingestion_preserves_scope_through_its_dedicated_team_argument(monkeypatch):
    f = preparation_setup()
    persist = Mock()
    monkeypatch.setattr("agent.services.task_queue_service.update_local_task_status", persist)
    f.adapter._queue = TaskQueueService()
    receipt = f.adapter.submit(f.command)
    assert receipt.accepted
    persist.assert_called_once()
    fields = persist.call_args.kwargs
    assert fields["team_id"] == "team-1" and fields["organization_id"] == "organization-1"
    assert fields["tenant_id"] == "tenant-1" and fields["project_id"] == "project-1"
    assert fields["parent_task_id"] == f.parent.id and fields["task_kind"] == "pi_coding_agent"
    assert fields["derivation_reason"] == "native_graph_hub_delegation"
    assert "source" not in fields  # Source is event metadata, not a TaskDB column.


@pytest.mark.parametrize("mutation", [
    "missing", "tenant", "project", "terminal", "owner", "unbound", "bundle", "partial_scope",
])
def test_missing_foreign_or_terminal_control_task_never_copies_or_queues(mutation):
    f = preparation_setup()
    if mutation == "missing":
        f.tasks.values.clear()
    elif mutation == "tenant":
        f.parent.tenant_id = "foreign"
    elif mutation == "project":
        f.parent.project_id = None
    elif mutation == "terminal":
        f.parent.status = "completed"
    elif mutation == "owner":
        f.original.task_id = "foreign"
    elif mutation == "unbound":
        f.original.task_id = None
    elif mutation == "partial_scope":
        f.parent.organization_id = None  # A team must not be silently detached from its organization.
    else:
        f.bundles.values.clear()
    with pytest.raises(ValueError):
        f.adapter.submit(f.command)
    assert not f.queue.ingested_values
    f.bundles.save.assert_not_called()


@pytest.mark.parametrize("metadata", [
    {"context_bundle_mode": "other"}, {"context_policy_id": "policy-1"},
    {"context_bundle_mode": "control_task"},
    {"context_bundle_mode": "control_task", "context_policy_id": "policy-1", "context_destination_id": "bad\n"},
])
def test_incomplete_or_unknown_context_request_never_silently_omits_context(metadata):
    f = preparation_setup()
    command = replace(f.command, node=replace(f.command.node, metadata=metadata))
    with pytest.raises(ValueError):
        f.adapter.submit(command)
    assert not f.queue.ingested_values
    f.bundles.save.assert_not_called()


def test_declared_context_requires_an_available_preparer():
    f = preparation_setup()
    f.adapter._context_preparer = None
    with pytest.raises(ValueError, match="native_context_preparer_unavailable"):
        f.adapter.submit(f.command)
    assert not f.queue.ingested_values


def test_interrupted_task_ingestion_reuses_exact_snapshot_but_refuses_changed_source():
    f = preparation_setup()
    ingest = f.queue.ingest_task
    f.queue.ingest_task = Mock(side_effect=RuntimeError("synthetic_ingest_failure"))
    with pytest.raises(RuntimeError, match="synthetic_ingest_failure"):
        f.adapter.submit(f.command)
    assert f.bundles.save.call_count == 1 and len(f.tasks.values) == 1
    original = copy.deepcopy(f.original.chunks)
    f.original.chunks[0]["content"] = "mutated source"
    f.queue.ingest_task = ingest
    with pytest.raises(ValueError, match="native_context_snapshot_conflict"):
        f.adapter.submit(f.command)
    assert not f.queue.ingested_values and f.bundles.save.call_count == 1
    f.original.chunks = original
    assert f.adapter.submit(f.command).accepted and f.bundles.save.call_count == 1


@pytest.mark.parametrize("mutation", ["input", "node", "fence", "typed_value"])
def test_same_command_id_with_changed_payload_is_not_an_idempotent_submission(mutation):
    f = preparation_setup()
    assert f.adapter.submit(f.command).accepted
    changed = replace(f.command, input_data={"different": True})
    if mutation == "node":
        changed = replace(f.command, node=replace(f.command.node, metadata={}))
    elif mutation == "fence":
        changed = replace(f.command, fencing_token=2)
        with pytest.raises(ValueError, match="native_provider_context_scope_mismatch"):
            f.adapter.submit(changed)  # Existing signed profile guard rejects even before duplicate lookup.
        assert len(f.queue.ingested_values) == f.bundles.save.call_count == 1
        return
    elif mutation == "typed_value":
        stored = f.tasks.get_by_id(f.queue.ingested_values[0]["task_id"])
        stored.worker_execution_context["native_node_command"]["fencing_token"] = True
        changed = f.command
    receipt = f.adapter.submit(changed)
    assert not receipt.accepted and receipt.reason_code == "native_hub_task_id_conflict"
    assert len(f.queue.ingested_values) == f.bundles.save.call_count == 1


@pytest.mark.parametrize("mutation", ["empty", "many", "large", "approval", "conflict", "remote", "unknown"])
def test_malformed_or_overlarge_retrieval_never_reaches_persistence(mutation):
    f = preparation_setup()
    if mutation == "empty":
        f.original.chunks = []
    elif mutation == "many":
        f.original.chunks *= 33
    elif mutation == "large":
        f.original.chunks[0]["content"] = "x" * 24001
    elif mutation == "approval":
        f.original.chunks[0]["metadata"]["approval_override_id"] = "untrusted"
    elif mutation == "conflict":
        f.original.chunks[0]["text"] = "conflicting content"
    elif mutation == "remote":
        f.original.chunks[0]["metadata"]["file"] = "https://remote.invalid/private"
    else:
        f.original.chunks[0]["engine"] = "unknown"
    with pytest.raises(ValueError):
        f.adapter.submit(f.command)
    f.bundles.save.assert_not_called()


def test_actual_sql_bundle_store_is_idempotent_and_never_overwrites(tmp_path):
    f = preparation_setup()
    engine = create_engine("sqlite:///" + str(tmp_path / "bundles.sqlite3"))
    ContextBundleDB.__table__.create(engine)

    class Repository:
        def get_by_id(self, bundle_id):
            with Session(engine) as session:
                return session.get(ContextBundleDB, bundle_id)

        def save(self, bundle):
            with Session(engine) as session:
                session.add(bundle)
                session.commit()
                session.refresh(bundle)
                return bundle

    repository = Repository()
    f.service._snapshots = HubNativeContextSnapshotStore(repository)
    first = f.service.prepare(command=f.command, hub_task_id="child")
    second = f.service.prepare(command=f.command, hub_task_id="child")
    assert first == second and repository.get_by_id(first.bundle_id).task_id == "child"
    f.original.chunks[0]["content"] = "different"
    with pytest.raises(ValueError, match="native_context_snapshot_conflict"):
        f.service.prepare(command=f.command, hub_task_id="child")
    assert repository.get_by_id(first.bundle_id).chunks[0]["content"] == "def example(): return 1"
    engine.dispose()


@pytest.mark.parametrize("same", [True, False])
def test_concurrent_insert_accepts_only_the_exact_existing_snapshot(same):
    f = preparation_setup()
    save = f.bundles.save.side_effect

    def race(bundle):
        save(bundle)
        if not same:
            bundle.task_id = "foreign"
        raise IntegrityError("synthetic insert", {}, Exception("duplicate"))

    f.bundles.save.side_effect = race
    if same:
        assert f.adapter.submit(f.command).accepted
    else:
        with pytest.raises(ValueError, match="native_context_snapshot_conflict"):
            f.adapter.submit(f.command)
        assert not f.queue.ingested_values


def test_app_owned_production_preparer_uses_existing_registry(app):
    f = preparation_setup()
    app.extensions["repository_registry"] = SimpleNamespace(task_repo=f.tasks, context_bundle_repo=f.bundles)
    with app.app_context():
        prepared = HubNativeContextPreparer().prepare(command=f.command, hub_task_id="child")
    assert prepared.parent_task_id == f.parent.id and f.bundles.get_by_id(prepared.bundle_id).task_id == "child"
    assert json.loads(f.service._snapshots._repository.save.call_args.args[0].model_dump_json())["context_text"] is None


@pytest.mark.parametrize("mutation", [None, "content", "parent", "policy", "command", "bundle", "lineage"])
def test_prepared_snapshot_is_revalidated_before_context_release(mutation):
    f = preparation_setup()
    receipt = f.adapter.submit(f.command)
    task = vars(f.tasks.get_by_id(receipt.hub_task_id))
    bundle = f.bundles.get_by_id(task["context_bundle_id"])
    if mutation == "content":
        bundle.chunks[0]["content"] = "mutated"
    elif mutation == "parent":
        task["parent_task_id"] = "foreign"
    elif mutation == "policy":
        bundle.bundle_metadata["native_context_access"]["policy_id"] = "foreign"
    elif mutation == "command":
        bundle.bundle_metadata["native_context_snapshot"]["command_digest"] = "a" * 64
    elif mutation == "bundle":
        task["context_bundle_id"] = "foreign"
    elif mutation == "lineage":
        bundle.bundle_metadata["native_context_snapshot"]["source_task_id"] = "foreign"
    if mutation is None:
        validate_prepared_native_context(task=task, bundle=bundle, command=f.command.to_dict())
    else:
        with pytest.raises(ValueError, match="native_context_snapshot_binding_mismatch"):
            validate_prepared_native_context(task=task, bundle=bundle, command=f.command.to_dict())
