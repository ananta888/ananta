"""The reference observer never substitutes missing counters with zero or success."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from tests.meet_dialog_resource_observation import DialogResourceObservation
from tests.test_meet_dialog_resources import value


def sample(active=1, time=1_000_000):
    result = value()
    result["slots"]["active"] = active
    result["sampled_monotonic_us"] = time
    result["cgroup"]["memory_limit_bytes"] = 1024**3
    return result


def test_disabled_observer_never_contacts_worker():
    transport, record = Mock(), Mock()
    observer = DialogResourceObservation(False, [transport])
    observer.capture(1)
    observer.record(record)
    transport.observe_dialog_resources.assert_not_called()
    record.assert_not_called()


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("memory_bytes", None),
        ("memory_bytes", 1024**3),
        ("memory_limit_bytes", 2 * 1024**3),
        ("pids", 257),
        ("cpu_usage_us", None),
    ],
)
def test_missing_or_over_budget_counters_cannot_pass_reference_profile(field, replacement):
    row = sample()
    row["cgroup"][field] = replacement
    transport = Mock(observe_dialog_resources=Mock(return_value=row))
    with pytest.raises((AssertionError, TypeError)):
        DialogResourceObservation(True, [transport]).capture(1)


def test_resource_samples_drop_nonce_and_classify_unavailable_worker_explicitly():
    first, second = Mock(), Mock()
    first.observe_dialog_resources.return_value = sample()
    second.observe_dialog_resources.return_value = sample()
    observer = DialogResourceObservation(True, [first, second])
    observer.capture(1)
    second.observe_dialog_resources.return_value = sample(0, 2_000_000)
    observer.capture(0, unavailable=(0,))
    record = Mock()
    observer.record(record)
    report = record.call_args.args[1]
    assert report["samples"][1][0] is None
    assert "nonce" not in repr(report)
    assert report["production_release_evidence"] is False and report["gpu_utilization"] == "unverified"
    first.observe_dialog_resources.assert_called_once()


def test_observed_cpu_progress_must_fit_two_core_reference_budget():
    initial = sample()
    later = deepcopy(initial)
    later["sampled_monotonic_us"] += 1_000_000
    later["cgroup"]["cpu_usage_us"] += 2_250_001
    transport = Mock(observe_dialog_resources=Mock(side_effect=[initial, later]))
    observer = DialogResourceObservation(True, [transport])
    observer.capture(1)
    with pytest.raises(AssertionError, match="two-core"):
        observer.capture(1)
