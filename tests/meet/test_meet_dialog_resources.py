"""Numeric observations neither dispatch work nor prove GPU/publication readiness."""

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_dialog_resources import request_signature, response_signature, validate_observation
from worker.meet_media.contract import encode
from worker.meet_media.dialog_resources import cgroup_snapshot, observe_resources
from worker.meet_media.dialog_slots import DialogSlots

KEY = b"synthetic-resource-observation-key"
NONCE = "a" * 32
QUERY = encode({"schema": "ananta.meet-dialog-resources-query.v1", "nonce": NONCE})
COUNTERS = {"memory_bytes": 1024, "memory_limit_bytes": 4096, "cpu_usage_us": 500, "pids": 2}


def value():
    return {
        "schema": "ananta.meet-dialog-resources.v1",
        "nonce": NONCE,
        "sampled_monotonic_us": 1_000_000,
        "slots": {"capacity": 2, "active": 1},
        "cgroup": dict(COUNTERS),
    }


def test_closed_copied_projection_contains_no_execution_or_authority_fields():
    source = value()
    observed = validate_observation(source, NONCE)
    observed["slots"]["active"] = 0
    observed["cgroup"]["memory_bytes"] = 0
    assert source["slots"]["active"] == 1 and source["cgroup"]["memory_bytes"] == 1024


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("nonce", "b" * 32),
        ("sampled_monotonic_us", True),
        ("sampled_monotonic_us", 2**53),
        ("task_id", "foreign"),
        ("slots", {}),
        ("cgroup", {}),
        ("allowed", True),
    ],
)
def test_bad_or_broadened_observation_is_never_repaired(field, replacement):
    with pytest.raises(ValueError, match="resources_invalid"):
        validate_observation(value() | {field: replacement}, NONCE)


@pytest.mark.parametrize(
    "field,replacement",
    [("capacity", 0), ("capacity", 5), ("capacity", True), ("active", -1), ("active", 3), ("active", False)],
)
def test_slot_counts_are_strict_bounded_observations(field, replacement):
    source = value()
    source["slots"][field] = replacement
    with pytest.raises(ValueError, match="resources_invalid"):
        validate_observation(source, NONCE)


@pytest.mark.parametrize("replacement", [-1, 1.2, True, "0", 2**53])
def test_resource_counts_never_coerce_wrong_types(replacement):
    source = value()
    source["cgroup"]["memory_bytes"] = replacement
    with pytest.raises(ValueError, match="resources_invalid"):
        validate_observation(source, NONCE)


def test_cgroup_reads_are_allowlisted_and_cumulative_not_invented_cpu_percent():
    read = Mock(side_effect=["1024", "4096", "usage_usec 500\nuser_usec 300\nsystem_usec 200", "2"])
    assert cgroup_snapshot(read) == COUNTERS
    assert [call.args for call in read.call_args_list] == [
        ("memory.current",),
        ("memory.max",),
        ("cpu.stat",),
        ("pids.current",),
    ]


@pytest.mark.parametrize("cpu", ["", "usage_usec 5\nusage_usec 6", "usage_usec", "usage_usec 1 2", "usage_usec -1"])
def test_partial_or_unlimited_cgroups_are_explicitly_unavailable(cpu):
    read = Mock(side_effect=[OSError("PRIVATE-MARKER"), "max", cpu, "2"])
    assert cgroup_snapshot(read) == {"memory_bytes": None, "memory_limit_bytes": None, "cpu_usage_us": None, "pids": 2}
    result = value() | {"cgroup": cgroup_snapshot(Mock(side_effect=OSError("PRIVATE-MARKER")))}
    assert validate_observation(result, NONCE)["cgroup"] == dict.fromkeys(COUNTERS)
    assert "PRIVATE-MARKER" not in repr(result)


def test_signature_domains_and_reply_challenge_are_not_interchangeable():
    from ananta_contracts.meet_dialog import request_signature as dispatch_signature

    raw = encode(value())
    assert request_signature(KEY, QUERY) != dispatch_signature(KEY, QUERY)
    assert response_signature(KEY, QUERY, raw) != request_signature(KEY, raw)
    assert response_signature(KEY, QUERY, raw) != response_signature(KEY, QUERY + b" ", raw)


def test_unauthorized_or_malformed_query_never_samples_or_opens_a_slot():
    executor, sample = Mock(), Mock()
    with pytest.raises(ValueError, match="unauthorized"):
        observe_resources(KEY, QUERY, "bad", executor, sample=sample)
    bad = encode({"schema": "ananta.meet-dialog-resources-query.v1", "nonce": NONCE, "execute": True})
    with pytest.raises(ValueError, match="query_invalid"):
        observe_resources(KEY, bad, request_signature(KEY, bad), executor, sample=sample)
    executor.assert_not_called()
    executor.slots.snapshot.assert_not_called()
    sample.assert_not_called()


def test_real_slots_are_observed_without_acquisition_or_dispatch():
    slots = DialogSlots(2)
    assert slots.acquire(blocking=False)
    executor = SimpleNamespace(slots=slots, start=Mock())
    result = observe_resources(
        KEY, QUERY, request_signature(KEY, QUERY), executor, sample=lambda: dict(COUNTERS), clock=lambda: 1
    )
    assert result == value()
    executor.start.assert_not_called()
    assert slots.snapshot() == {"capacity": 2, "active": 1}
    slots.release()
    assert slots.snapshot()["active"] == 0
    with pytest.raises(ValueError):
        slots.release()
    assert slots.snapshot()["active"] == 0


def test_concurrent_execution_slots_remain_bounded_and_return_to_zero():
    slots, barrier = DialogSlots(2), threading.Barrier(4)
    snapshots = []
    lock = threading.Lock()

    def attempt(_):
        acquired = slots.acquire(blocking=False)
        barrier.wait(timeout=3)
        with lock:
            snapshots.append(deepcopy(slots.snapshot()))
        barrier.wait(timeout=3)
        if acquired:
            slots.release()
        return acquired

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(attempt, range(4))) == 2
    assert all(row == {"capacity": 2, "active": 2} for row in snapshots)
    assert slots.snapshot()["active"] == 0


def test_existing_blocking_and_timeout_semaphore_calls_still_work():
    slots = DialogSlots(1)
    assert slots.acquire()
    assert slots.acquire(timeout=0.01) is False
    slots.release()
    assert slots.acquire(timeout=0.01)
    slots.release()
