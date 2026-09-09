"""Source-local clock observations cannot revive epochs or hide stalled clocks."""

from copy import deepcopy

import pytest

from tests.test_meet_media_timing_contract import fixture
from worker.meet_media.media_timing_gate import MediaTimingGate


def setup():
    clock = [100.0]
    return clock, MediaTimingGate(1, {"speech", "avatar", "screen"}, clock=lambda: clock[0])


def advance(payload, delta=100_000):
    result = deepcopy(payload)
    result["now_us"] += delta
    for source in result["sources"].values():
        source["position_at_us"] += delta
        source["observed_at_us"] += delta
        source["position_us"] += delta
    return result


def test_common_timeline_is_independent_of_host_monotonic_offset_and_return_mutation():
    clock, gate = setup()
    value = fixture()
    accepted = gate.accept(value)
    accepted["sources"]["speech"]["generation"] = 3000
    clock[0] += 0.1
    assert gate.accept(advance(value))["sources"]["speech"]["generation"] == 1


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda p: p.update(epoch=2), "scope_changed"),
        (lambda p: p["sources"]["speech"].update(generation=0), "invalid"),
        (lambda p: p["sources"]["speech"].update(state="failed"), "source_failed"),
        (
            lambda p: p["sources"]["speech"].update(started_at_us=1_100_000, origin_position_us=120_000),
            "source_changed",
        ),
    ],
)
def test_bad_observation_permanently_closes_quality_gate(mutation, code):
    _, gate = setup()
    original = fixture()
    gate.accept(original)
    value = advance(original)
    mutation(value)
    with pytest.raises(ValueError, match="meet_media_timing_" + code):
        gate.accept(value)
    with pytest.raises(ValueError, match="meet_media_timing_closed"):
        gate.accept(original)


def test_disappeared_source_generation_cannot_reappear_but_fresh_independent_generation_can():
    _, gate = setup()
    value = fixture()
    gate.accept(value)
    gate.accept(value | {"sources": {}})
    fresh = deepcopy(value)
    fresh["sources"]["speech"]["generation"] = 2
    assert gate.accept(fresh) == fresh
    with pytest.raises(ValueError, match="meet_media_timing_generation_stale"):
        gate.accept(value)


@pytest.mark.parametrize("host, browser", [(100.501, 2_000_000), (100, 2_500_001), (99.9, 2_000_000)])
def test_stalled_or_regressed_clock_cannot_be_hidden_by_current_source_fields(host, browser):
    clock, gate = setup()
    value = fixture() | {"sources": {}}
    gate.accept(value)
    clock[0] = host
    with pytest.raises(ValueError, match="meet_media_timing_clock_changed"):
        gate.accept(value | {"now_us": browser})


def test_unassigned_source_is_not_admitted_by_a_valid_clock_shape():
    gate = MediaTimingGate(1, {"screen"})
    with pytest.raises(ValueError, match="meet_media_timing_scope_changed"):
        gate.accept(fixture())
