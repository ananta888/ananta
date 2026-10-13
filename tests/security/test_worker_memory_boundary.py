"""Headless synthetic Hub transport; no Worker flags confer memory authority."""

from dataclasses import replace

from ananta_contracts.memory_access import MemoryOperation
from tests.security.memory_fixtures import SyntheticWorkerMemoryAuthorization
from worker.core.memory_stores import WorkerMemoryStores
from worker.core.session_search import IndexedDocument, SearchTarget, SessionSearchIndex


def write(stores, **changes):
    request = dict(task_id="t1", has_memory_write_capability=True, hub_approved=True)
    request.update(changes)
    return stores.write("worker_session_memory", "canary", "synthetic private memory", **request)


def test_flags_and_direct_store_access_cannot_bypass_missing_hub_port():
    stores = WorkerMemoryStores()
    assert not write(stores).success
    assert not stores.session.write("canary", "secret", task_id="t1", hub_approved=True).success
    assert stores.session.read("canary") is None
    assert stores.session.snapshot() == []
    assert stores.session.search("secret") == []


def test_foreign_task_binding_run_drift_and_revoked_read_never_return_canary():
    authority = SyntheticWorkerMemoryAuthorization()
    stores = WorkerMemoryStores(authorization=authority)
    assert not write(stores, task_id="foreign-task").success
    assert write(stores).success
    authority.operations.remove(MemoryOperation.read)
    assert stores.session.read("canary") is None
    authority.operations.add(MemoryOperation.read)
    original = authority.namespace
    for changes in ({"tenant_id": "foreign"}, {"workspace_id": "foreign"},
                    {"task_id": "foreign"}, {"run_id": "another-run"}):
        authority.namespace = replace(original, **changes)
        assert stores.session.read("canary") is None
    authority.namespace = original
    assert stores.session.read("canary").value == "synthetic private memory"


def test_updates_require_independent_grant_and_returned_entries_are_detached():
    authority = SyntheticWorkerMemoryAuthorization()
    stores = WorkerMemoryStores(authorization=authority)
    result = write(stores)
    result.entry.value = "changed external alias"
    assert stores.session.read("canary").value == "synthetic private memory"
    authority.operations.remove(MemoryOperation.update)
    assert not write(stores).success
    assert stores.session.read("canary").value == "synthetic private memory"


def test_long_term_boolean_approval_remains_a_proposal_and_worker_cannot_promote():
    stores = WorkerMemoryStores(authorization=SyntheticWorkerMemoryAuthorization())
    result = stores.write("proposed_long_term_memory", "proposal", "synthetic proposed fact",
                          task_id="t1", has_memory_write_capability=True, hub_approved=True)
    assert result.success and result.entry.is_proposal and not result.entry.approved
    assert stores.long_term.approve_proposal("proposal") is False
    assert not stores.long_term.read("proposal").approved


def test_unconfigured_search_cannot_index_or_recover_memory():
    index = SessionSearchIndex()
    index.index_task(task_id="t1", summary="synthetic canary", status="completed")
    assert index.search("canary") == []
    assert index.last_reason_code == "memory_hub_authorization_required"


def test_search_uses_hub_namespace_not_caller_empty_or_foreign_filters():
    authority = SyntheticWorkerMemoryAuthorization()
    index = SessionSearchIndex(authorization=authority)
    doc = IndexedDocument(SearchTarget.task, "t1", "synthetic canary")
    index.index(doc)
    doc.content = "mutated alias"
    assert index.search("canary")[0].tenant_id == authority.namespace.tenant_id
    assert index.search("canary", tenant_id="foreign") == []
    assert index.search("canary", project_id="foreign") == []
    original = authority.namespace
    authority.namespace = replace(original, run_id="other-run")
    assert index.search("canary") == []
    authority.namespace = original
    authority.operations.remove(MemoryOperation.read)
    assert index.search("canary") == []


def test_search_rejects_foreign_import_and_stale_transport_authority():
    authority = SyntheticWorkerMemoryAuthorization()
    index = SessionSearchIndex(authorization=authority)
    index.index(IndexedDocument(SearchTarget.task, "foreign", "private canary", tenant_id="foreign"))
    assert index.last_reason_code == "memory_namespace_mismatch"
    assert index.search("canary") == []
    authority.expires_at = 1
    stores = WorkerMemoryStores(authorization=authority)
    assert not write(stores).success
    index.index_task(task_id="t1", summary="canary", status="done")
    assert index.search("canary") == []
