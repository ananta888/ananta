"""Actual Hub SQL binding checks; synthetic identities, no release evidence."""

from dataclasses import replace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from agent.db_models import GoalDB, MemoryRunPolicyDB, ProjectDB, TaskDB, WorkerJobDB, WorkerSlotLeaseDB
from agent.repositories.memory_audit import SQLMemoryAuditRepository
from agent.repositories.memory_policy import SQLMemoryRunPolicyRepository
from agent.services.legacy_memory_access import ScopedResultMemoryReader
from agent.services.memory_access_context import MemoryAccessContext, bind_memory_access, current_memory_access
from agent.services.memory_audit_service import MemoryAuditService
from agent.services.memory_channel_registry import MemoryChannelRegistry
from agent.services.memory_policy_gateway import MemoryAccessError, MemoryPolicyGateway
from ananta_contracts.memory_access import MemoryOperation, MemoryScope, MemorySecurityMetadata
from tests.security.memory_fixtures import ACTOR, NAMESPACE, grant, policy


@pytest.fixture
def persisted(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'policy.sqlite3'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(ProjectDB(tenant_id=ACTOR.tenant_id, project_id=NAMESPACE.workspace_id,
                              name="Synthetic project", created_by_subject_id=ACTOR.subject))
        session.commit()
        session.add(GoalDB(id=NAMESPACE.goal_id, goal="Synthetic goal", tenant_id=ACTOR.tenant_id,
                           project_id=NAMESPACE.workspace_id))
        session.commit()
        session.add(TaskDB(id=NAMESPACE.task_id, title="Synthetic task", tenant_id=ACTOR.tenant_id,
                           project_id=NAMESPACE.workspace_id, goal_id=NAMESPACE.goal_id,
                           current_worker_job_id="synthetic-job", status="assigned"))
        session.add(WorkerJobDB(id="synthetic-job", parent_task_id=NAMESPACE.task_id, worker_url="http://synthetic",
                               status="delegated", selected_worker_id=ACTOR.subject, slot_lease_id="synthetic-lease"))
        session.add(WorkerSlotLeaseDB(id="synthetic-lease", worker_id=ACTOR.subject, worker_job_id="synthetic-job",
                                     parent_task_id=NAMESPACE.task_id, deadline_at=1000))
        session.commit()
    repository = SQLMemoryRunPolicyRepository(engine, clock=lambda: 100)
    value = policy((grant(channel="hub-result-memory", scope=MemoryScope.task),))
    repository.reserve(value, worker_job_id="synthetic-job", dispatch_lease_id="synthetic-lease")
    yield engine, repository, value
    engine.dispose()


def test_current_policy_requires_exact_actor_and_persisted_binding(persisted):
    _engine, repository, value = persisted
    assert repository.current(actor=ACTOR, run_id=NAMESPACE.run_id) == value
    assert repository.current(actor=replace(ACTOR, tenant_id="foreign"), run_id=NAMESPACE.run_id) is None
    assert repository.current(actor=replace(ACTOR, subject="foreign"), run_id=NAMESPACE.run_id) is None
    assert repository.current(actor=ACTOR, run_id="unknown") is None


@pytest.mark.parametrize("model,identity,field,value", [
    (TaskDB, NAMESPACE.task_id, "current_worker_job_id", "another-job"),
    (TaskDB, NAMESPACE.task_id, "tenant_id", "another-tenant"),
    (TaskDB, NAMESPACE.task_id, "project_id", "another-project"),
    (TaskDB, NAMESPACE.task_id, "goal_id", None),
    (TaskDB, NAMESPACE.task_id, "status", "completed"),
    (WorkerJobDB, "synthetic-job", "status", "completed"),
    (WorkerJobDB, "synthetic-job", "selected_worker_id", "another-worker"),
    (WorkerJobDB, "synthetic-job", "slot_lease_id", "another-lease"),
    (WorkerSlotLeaseDB, "synthetic-lease", "status", "released"),
    (WorkerSlotLeaseDB, "synthetic-lease", "deadline_at", 99),
    (WorkerSlotLeaseDB, "synthetic-lease", "worker_job_id", "another-job"),
    (WorkerSlotLeaseDB, "synthetic-lease", "parent_task_id", "another-task"),
])
def test_changed_live_dispatch_binding_revokes_access(persisted, model, identity, field, value):
    engine, repository, _policy = persisted
    with Session(engine) as session:
        row = session.get(model, identity)
        setattr(row, field, value)
        session.add(row)
        session.commit()
    assert repository.current(actor=ACTOR, run_id=NAMESPACE.run_id) is None


@pytest.mark.parametrize("status", [
    "completed", "done", "complete", "failed", "cancelled", "canceled",
    "verification_failed", "verification-failed", "skipped", "aborted", "timeout", "archived",
])
def test_every_terminal_task_status_and_legacy_alias_denies_dispatch_memory(persisted, status):
    engine, repository, _policy = persisted
    with Session(engine) as session:
        task = session.get(TaskDB, NAMESPACE.task_id)
        task.status = status
        session.add(task)
        session.commit()
    assert repository.current(actor=ACTOR, run_id=NAMESPACE.run_id) is None


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "timeout", "aborted", "archived"])
def test_committed_terminal_task_discards_only_its_runtime_scratchpads(persisted, monkeypatch, status):
    from flask import Flask

    import agent.database as database
    from agent.services.memory_runtime import HubMemoryRuntime
    from agent.services.task_runtime_service import update_local_task_status

    engine, _repository, _policy = persisted
    monkeypatch.setattr(database, "engine", engine)
    runtime = HubMemoryRuntime(engine, clock=lambda: 100)
    namespace = replace(NAMESPACE, run_id="synthetic-scratchpad-run")
    value = policy((grant(namespace=namespace),), namespace=namespace)
    runtime.policies.reserve(value, worker_job_id="synthetic-job", dispatch_lease_id="synthetic-lease")
    runtime.scratchpad.append(actor=ACTOR, run_id=namespace.run_id, key="scratch", value="terminal canary")
    foreign = replace(namespace, tenant_id="foreign-tenant")
    runtime._scratchpad_store.append(foreign.key(MemoryScope.run), "scratch", "foreign canary",
                                    expires_at=900, now=100)
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    app.extensions["hub_memory_runtime"] = runtime
    with app.app_context():
        update_local_task_status(NAMESPACE.task_id, status, force=True)
    with Session(engine) as session:
        assert session.get(TaskDB, NAMESPACE.task_id).status == status
    assert runtime._scratchpad_store.read(namespace.key(MemoryScope.run), "scratch", now=100) is None
    assert runtime._scratchpad_store.read(foreign.key(MemoryScope.run), "scratch", now=100) == "foreign canary"
    with pytest.raises(MemoryAccessError):
        runtime.scratchpad.read(actor=ACTOR, run_id=namespace.run_id, key="scratch")


def test_scratchpad_is_discarded_even_when_session_revocation_fails(persisted, monkeypatch):
    from agent.services.memory_runtime import HubMemoryRuntime

    engine, _repository, _policy = persisted
    runtime = HubMemoryRuntime(engine, clock=lambda: 100)
    namespace = replace(NAMESPACE, run_id="synthetic-scratchpad-run")
    value = policy((grant(namespace=namespace),), namespace=namespace)
    runtime.policies.reserve(value, worker_job_id="synthetic-job", dispatch_lease_id="synthetic-lease")
    runtime.scratchpad.append(actor=ACTOR, run_id=namespace.run_id, key="scratch", value="temporary canary")

    def unavailable(**_request):
        raise RuntimeError("private SQL connection credentials")

    monkeypatch.setattr(runtime.policies, "revoke", unavailable)
    with pytest.raises(MemoryAccessError, match="^memory_policy_unavailable$"):
        runtime._revoke(ACTOR, namespace.run_id, namespace=namespace)
    assert runtime._scratchpad_store.read(namespace.key(MemoryScope.run), "scratch", now=100) is None


def test_terminal_cleanup_failure_remains_denied_bounded_and_reapable(persisted, monkeypatch, caplog):
    from flask import Flask

    import agent.database as database
    from agent.services.memory_runtime import HubMemoryRuntime
    from agent.services.task_runtime_service import update_local_task_status

    engine, _repository, _policy = persisted
    monkeypatch.setattr(database, "engine", engine)
    clock = [100]
    runtime = HubMemoryRuntime(engine, clock=lambda: clock[0])
    namespace = replace(NAMESPACE, run_id="synthetic-scratchpad-run")
    value = policy((grant(namespace=namespace),), namespace=namespace)
    runtime.policies.reserve(value, worker_job_id="synthetic-job", dispatch_lease_id="synthetic-lease")
    runtime.scratchpad.append(actor=ACTOR, run_id=namespace.run_id, key="scratch", value="orphan canary")

    def unavailable(**_request):
        raise RuntimeError("private cleanup credentials")

    monkeypatch.setattr(runtime._scratchpad_store, "discard_task", unavailable)
    app = Flask(__name__)
    app.config["ROLE"] = "hub"
    app.extensions["hub_memory_runtime"] = runtime
    with app.app_context():
        update_local_task_status(NAMESPACE.task_id, "timeout", force=True)
    assert "Hub scratchpad cleanup failed" in caplog.text
    assert "private cleanup credentials" not in caplog.text
    with pytest.raises(MemoryAccessError):
        runtime.scratchpad.read(actor=ACTOR, run_id=namespace.run_id, key="scratch")
    clock[0] = 401
    assert runtime.scratchpad.reap_expired() == 1


def test_mutated_policy_and_explicit_revocation_fail_closed(persisted):
    engine, repository, _policy = persisted
    repository.revoke(actor=replace(ACTOR, subject="foreign"), run_id=NAMESPACE.run_id)
    assert repository.current(actor=ACTOR, run_id=NAMESPACE.run_id)
    with Session(engine) as session:
        row = session.get(MemoryRunPolicyDB, NAMESPACE.run_id)
        row.policy = {**row.policy, "expires_at": 9999}
        session.add(row)
        session.commit()
    assert repository.current(actor=ACTOR, run_id=NAMESPACE.run_id) is None
    repository.revoke(actor=ACTOR, run_id=NAMESPACE.run_id)
    assert repository.current(actor=ACTOR, run_id=NAMESPACE.run_id) is None


def test_binding_context_resets_after_exception_and_does_not_grant_by_itself(persisted):
    engine, repository, _policy = persisted
    gate = MemoryPolicyGateway(
        policies=repository, channels=MemoryChannelRegistry(frozenset({"hub-result-memory"})),
        audit=MemoryAuditService(repository=SQLMemoryAuditRepository(engine), clock=lambda: 100),
        clock=lambda: 100,
    )
    context = MemoryAccessContext(ACTOR, NAMESPACE.run_id, gate)
    with pytest.raises(RuntimeError):
        with bind_memory_access(context):
            assert current_memory_access() == context
            raise RuntimeError("synthetic exception")
    with pytest.raises(MemoryAccessError, match="run_context_required"):
        current_memory_access()
    repository.revoke(actor=ACTOR, run_id=NAMESPACE.run_id)
    with bind_memory_access(context):
        with pytest.raises(MemoryAccessError):
            gate.authorize_current(actor=ACTOR, run_id=NAMESPACE.run_id, operation=MemoryOperation.read,
                                   channel_id="hub-result-memory", security=MemorySecurityMetadata())


def test_legacy_reader_calls_only_namespace_port_and_never_legacy_lookup(persisted):
    engine, repository, _policy = persisted
    audit = SQLMemoryAuditRepository(engine)
    gate = MemoryPolicyGateway(policies=repository,
                               channels=MemoryChannelRegistry(frozenset({"hub-result-memory"})),
                               audit=MemoryAuditService(repository=audit, clock=lambda: 100), clock=lambda: 100)

    class StrictRepository:
        def get_by_task(self, *_args):
            pytest.fail("legacy unscoped lookup")

        def get_by_namespace(self, namespace, scope):
            assert namespace == NAMESPACE and scope == MemoryScope.task
            return []

    reader = ScopedResultMemoryReader(StrictRepository())
    with bind_memory_access(MemoryAccessContext(ACTOR, NAMESPACE.run_id, gate)):
        assert reader.get_by_task(NAMESPACE.task_id) == []
        assert reader.get_by_task("foreign-task") == []
    assert len(audit.list_events(tenant_id=ACTOR.tenant_id)) >= 2


def test_hub_read_session_uses_current_project_acl_and_revokes_on_exit(persisted):
    from flask import Flask, g

    from agent.services.memory_runtime import HubMemoryRuntime
    from agent.services.project_access_authority import ProjectAccessDeniedError

    engine, _repository, _policy = persisted
    runtime = HubMemoryRuntime(engine, clock=lambda: 100)
    app = Flask(__name__)

    class CurrentProjectAuthority:
        active = True

        def require(self, **request):
            assert request["tenant_id"] == ACTOR.tenant_id
            assert request["project_id"] == NAMESPACE.workspace_id
            if not self.active:
                raise ProjectAccessDeniedError(tenant_id=ACTOR.tenant_id, project_id=NAMESPACE.workspace_id)
            return object()

    authority = CurrentProjectAuthority()
    app.extensions["project_access_authority"] = authority
    with app.test_request_context("/synthetic-read"):
        g.user = {"sub": ACTOR.subject, "role": "admin", "tenant_id": ACTOR.tenant_id,
                  "project_id": NAMESPACE.workspace_id}
        g.auth_payload = {}
        g.is_admin = True
        with runtime.request_read(NAMESPACE.task_id) as context:
            admitted = runtime.policies.current(actor=ACTOR, run_id=context.run_id)
            assert admitted and all(grant.operations == (MemoryOperation.read,) for grant in admitted.capabilities)
            assert not context.run_id.startswith(("RUN_", "SRC_"))
            authority.active = False
            with pytest.raises(MemoryAccessError):
                context.gateway.authorize_current(actor=ACTOR, run_id=context.run_id,
                                                  operation=MemoryOperation.read, channel_id="hub-result-memory",
                                                  security=MemorySecurityMetadata())
        assert runtime.policies.current(actor=ACTOR, run_id=context.run_id) is None


def test_hub_result_session_requires_current_authenticated_worker_and_live_lease(persisted):
    from flask import Flask, g

    import agent.database as database
    from agent.repositories.memory import MemoryEntryRepository
    from agent.services.memory_runtime import HubMemoryRuntime
    from agent.services.result_memory_service import ResultMemoryService

    engine, _repository, _policy = persisted
    runtime = HubMemoryRuntime(engine, clock=lambda: 100)
    app = Flask(__name__)
    app.config["AGENT_ROLE"] = "hub"
    app.extensions["hub_memory_runtime"] = runtime
    original_engine = database.engine
    database.engine = engine
    try:
        with app.test_request_context("/synthetic-result"):
            g.service_identity = {"worker_id": ACTOR.subject}
            with runtime.worker_result(NAMESPACE.task_id, "synthetic-job"):
                service = ResultMemoryService(memory_entry_repository=MemoryEntryRepository())
                entry = service.record_worker_result_memory(
                    task_id=NAMESPACE.task_id, goal_id=NAMESPACE.goal_id, trace_id=None,
                    worker_job_id="synthetic-job", title="Synthetic result", output="synthetic completed work",
                    policy={"create_followup_artifact": False},
                )
                assert entry.memory_metadata["memory_namespace"]["tenant_id"] == ACTOR.tenant_id
                assert entry.memory_metadata["memory_security"]["trust_level"] == "untrusted"
                with Session(engine) as session:
                    lease = session.get(WorkerSlotLeaseDB, "synthetic-lease")
                    lease.status = "released"
                    session.add(lease)
                    session.commit()
                with pytest.raises(MemoryAccessError):
                    ResultMemoryService().record_worker_result_memory(
                        task_id=NAMESPACE.task_id, goal_id=NAMESPACE.goal_id, trace_id=None,
                        worker_job_id="synthetic-job", title="Denied", output="must not be persisted",
                    )
    finally:
        database.engine = original_engine


def test_migration_cli_preview_apply_and_rollback_are_bounded_and_do_not_create_evidence(persisted, capsys):
    import json

    from agent.db_models import MemoryEntryDB
    from scripts.migrate_memory_security import main

    engine, _repository, _policy = persisted
    original = {"approved": True, "memory_scope": "global"}
    with Session(engine) as session:
        session.add(MemoryEntryDB(id="synthetic-cli-memory", task_id=NAMESPACE.task_id,
                                  goal_id=NAMESPACE.goal_id, memory_metadata=original))
        session.commit()
    arguments = ["--database-url", str(engine.url), "--limit", "1"]
    assert main(arguments) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["production_release_evidence"] is False
    assert preview["result"]["dry_run"] is True and preview["result"]["would_update"] == 1
    with Session(engine) as session:
        assert session.get(MemoryEntryDB, "synthetic-cli-memory").memory_metadata == original
    assert main([*arguments, "--apply", "--batch-id", "synthetic-cli-batch"]) == 0
    capsys.readouterr()
    with Session(engine) as session:
        changed = session.get(MemoryEntryDB, "synthetic-cli-memory").memory_metadata
        assert changed["memory_namespace"]["tenant_id"] == ACTOR.tenant_id
        assert changed["memory_security"]["trust_level"] == "untrusted"
    assert main([*arguments, "--rollback", "--batch-id", "synthetic-cli-batch"]) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["result"]["restored"] == 1
    with Session(engine) as session:
        assert session.get(MemoryEntryDB, "synthetic-cli-memory").memory_metadata == original


def test_migration_cli_does_not_echo_secret_database_configuration(capsys):
    from scripts.migrate_memory_security import main

    assert main(["--database-url", "invalid+private://operator:super-secret@unavailable/database"]) == 2
    assert "super-secret" not in capsys.readouterr().out


def test_runtime_revocation_backend_failure_is_bounded(persisted, monkeypatch):
    from agent.services.memory_runtime import HubMemoryRuntime

    engine, _repository, _policy = persisted
    runtime = HubMemoryRuntime(engine, clock=lambda: 100)

    def unavailable(**_request):
        raise RuntimeError("private SQL connection credentials")

    monkeypatch.setattr(runtime.policies, "revoke", unavailable)
    with pytest.raises(MemoryAccessError, match="^memory_policy_unavailable$"):
        runtime._revoke(ACTOR, NAMESPACE.run_id)


@pytest.mark.parametrize("config, allowed", [
    ({"ROLE": "hub"}, True),
    ({"AGENT_ROLE": "hub"}, True),
    ({"ROLE": "worker", "AGENT_ROLE": "hub"}, False),
    ({"ROLE": "", "AGENT_ROLE": "hub"}, False),
    ({"ROLE": "worker"}, False),
    ({}, False),
])
def test_runtime_composition_uses_actual_bootstrap_role_and_rejects_worker_override(config, allowed):
    from flask import Flask

    from agent.services.memory_runtime import get_memory_runtime

    app = Flask(__name__)
    app.config.update(config)
    runtime = object()
    app.extensions["hub_memory_runtime"] = runtime
    with app.app_context():
        if allowed:
            assert get_memory_runtime() is runtime
        else:
            with pytest.raises(MemoryAccessError, match="^memory_hub_context_required$"):
                get_memory_runtime()
