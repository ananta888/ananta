from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent.adapters.ephemeral_scratchpad_store import InMemoryScratchpadStore, ScratchpadStorageError
from agent.services.ephemeral_scratchpad_service import EphemeralScratchpadService
from agent.services.memory_policy_gateway import MemoryAccessError
from ananta_contracts.memory_access import MemoryOperation, MemoryScope
from tests.security.memory_fixtures import ACTOR, NAMESPACE, gateway, grant, policy


def prepared(*, capabilities=None, store=None, ttl=10):
    clock = [100]
    gate, policies, audit = gateway(policy(capabilities), clock=lambda: clock[0])
    storage = InMemoryScratchpadStore() if store is None else store
    service = EphemeralScratchpadService(gateway=gate, store=storage, ttl_seconds=ttl, clock=lambda: clock[0])
    return service, storage, policies, audit, clock


def test_append_read_update_delete_and_terminal_cleanup():
    service, storage, _policies, audit, _clock = prepared()
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch")
    service.append(**request, value="untrusted text")
    assert service.read(**request) == "untrusted text"
    service.update(**request, value="changed text")
    assert service.read(**request) == "changed text"
    assert service.delete(**request)
    assert service.read(**request) is None
    service.append(**request, value="temporary")
    assert service.discard_run(NAMESPACE) == 1
    assert storage.read(NAMESPACE.key(MemoryScope.run), "scratch", now=100) is None
    assert len(audit.decisions) == 13
    assert "untrusted text" not in repr(audit.decisions)


def test_no_grant_means_no_storage_access():
    class ForbiddenStorage:
        def __getattr__(self, name):
            pytest.fail(f"Storage must not be touched on denial: {name}")

    service, _store, _policies, _audit, _clock = prepared(capabilities=(), store=ForbiddenStorage())
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        service.append(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch", value="data")


def test_append_permission_does_not_allow_overwrite_or_update():
    service, _store, _policies, _audit, _clock = prepared(
        capabilities=(grant(operations=(MemoryOperation.append, MemoryOperation.read)),),
    )
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch")
    service.append(**request, value="original")
    with pytest.raises(ScratchpadStorageError, match="entry_exists"):
        service.append(**request, value="overwritten")
    with pytest.raises(MemoryAccessError, match="capability_missing"):
        service.update(**request, value="overwritten")
    assert service.read(**request) == "original"


def test_scratchpad_does_not_leak_to_a_later_run_using_same_key():
    service, _storage, policies, _audit, _clock = prepared()
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch")
    service.append(**request, value="cross-task canary")
    next_namespace = replace(NAMESPACE, task_id="next-test-task", run_id="next-technical-test-run")
    policies.value = policy((grant(namespace=next_namespace),), namespace=next_namespace)
    assert service.read(actor=ACTOR, run_id=next_namespace.run_id, key="scratch") is None
    with pytest.raises(MemoryAccessError, match="run_binding_mismatch"):
        service.read(**request)


def test_expiration_and_orphan_reaping_free_capacity():
    store = InMemoryScratchpadStore(max_runs=1, max_entries_per_run=1)
    service, _store, _policies, _audit, clock = prepared(store=store)
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch")
    service.append(**request, value="temporary")
    clock[0] = 110
    assert service.reap_expired() == 1
    assert service.read(**request) is None
    service.append(**request, value="new value")
    assert service.read(**request) == "new value"


def test_terminal_policy_blocks_read_before_store_even_before_reaping():
    service, _store, policies, _audit, _clock = prepared()
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch")
    service.append(**request, value="temporary")
    policies.value = replace(policies.value, active=False)
    with pytest.raises(MemoryAccessError, match="run_inactive"):
        service.read(**request)
    assert service.discard_run(NAMESPACE) == 1


@pytest.mark.parametrize("operation", ["read", "append", "update"])
def test_policy_revoked_during_storage_does_not_return_content_or_keep_late_write(operation):
    class RevokingStore(InMemoryScratchpadStore):
        armed = False

        def read(self, *args, **kwargs):
            value = super().read(*args, **kwargs)
            if self.armed:
                policies.value = replace(policies.value, active=False)
            return value

        def _write(self, *args, **kwargs):
            super()._write(*args, **kwargs)
            if self.armed:
                policies.value = replace(policies.value, active=False)

    store = RevokingStore()
    service, _store, policies, _audit, _clock = prepared(store=store)
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch")
    if operation != "append":
        service.append(**request, value="existing canary")
    store.armed = True
    with pytest.raises(MemoryAccessError, match="run_inactive"):
        getattr(service, operation)(**request, **({} if operation == "read" else {"value": "late canary"}))
    assert store.discard(NAMESPACE.key(MemoryScope.run)) == 0


@pytest.mark.parametrize("value", [None, {}, b"binary", 123])
def test_only_text_values_are_accepted(value):
    service, _store, _policies, _audit, _clock = prepared()
    with pytest.raises(ScratchpadStorageError, match="value_type_invalid"):
        service.append(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch", value=value)


def test_unicode_byte_quota_does_not_count_only_characters():
    service, _store, _policies, _audit, _clock = prepared(
        store=InMemoryScratchpadStore(max_bytes_per_run=20, max_value_bytes=4),
    )
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="k")
    service.append(**request, value="🙂")
    with pytest.raises(ScratchpadStorageError, match="value_quota_exceeded"):
        service.update(**request, value="🙂🙂")
    assert service.read(**request) == "🙂"


def test_malformed_unicode_is_rejected_with_bounded_diagnostic():
    service, _store, _policies, _audit, _clock = prepared()
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="k")
    service.append(**request, value="original")
    with pytest.raises(ScratchpadStorageError, match="value_encoding_invalid"):
        service.update(**request, value="\ud800")
    assert service.read(**request) == "original"


def test_failed_byte_quota_update_preserves_previous_value():
    service, _store, _policies, _audit, _clock = prepared(
        store=InMemoryScratchpadStore(max_bytes_per_run=6, max_value_bytes=6),
    )
    request = dict(actor=ACTOR, run_id=NAMESPACE.run_id, key="k")
    service.append(**request, value="123")
    with pytest.raises(ScratchpadStorageError, match="byte_quota_exceeded"):
        service.update(**request, value="123456")
    assert service.read(**request) == "123"


def test_concurrent_appends_cannot_exceed_entry_quota():
    service, _store, _policies, _audit, _clock = prepared(store=InMemoryScratchpadStore(max_entries_per_run=1))

    def append(key):
        try:
            service.append(actor=ACTOR, run_id=NAMESPACE.run_id, key=key, value="data")
            return "ok"
        except ScratchpadStorageError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, ["first", "second"]))
    assert sorted(results) == ["ok", "scratchpad_entry_quota_exceeded"]


def test_run_quota_is_independent_of_per_run_entry_quota():
    store = InMemoryScratchpadStore(max_runs=1)
    store.append(("first-run",), "key", "value", expires_at=200, now=100)
    with pytest.raises(ScratchpadStorageError, match="run_quota_exceeded"):
        store.append(("second-run",), "key", "value", expires_at=200, now=100)
    assert store.discard(("first-run",)) == 1
    store.append(("second-run",), "key", "value", expires_at=200, now=100)


def test_expiry_is_capped_at_current_run_policy_deadline():
    service, store, policies, _audit, clock = prepared(ttl=300)
    policies.value = replace(policies.value, expires_at=900)
    clock[0] = 899
    service.append(actor=ACTOR, run_id=NAMESPACE.run_id, key="scratch", value="temporary")
    assert store.read(NAMESPACE.key(MemoryScope.run), "scratch", now=900) is None


def test_process_restart_has_no_persistent_state_to_recover():
    store = InMemoryScratchpadStore()
    namespace = NAMESPACE.key(MemoryScope.run)
    store.append(namespace, "scratch", "temporary", expires_at=200, now=100)
    replacement = InMemoryScratchpadStore()
    assert replacement.read(namespace, "scratch", now=100) is None
