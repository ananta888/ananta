"""Closed browser sequence adapter; signed Hub renewal never bypassed."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from worker.meet_media.companion_native_sequence import NativeCompanionSequence


def owner():
    return SimpleNamespace(page=SimpleNamespace(evaluate=Mock()), renew=Mock(), pump=SimpleNamespace(last_renew=0),
        client=SimpleNamespace(assignment={"session_id": "synthetic"}), avatar_generation=None,
        sequence_active=False, controls=SimpleNamespace(state={"controls": {"avatar": {"enabled": True}}}))


def test_prepare_validates_probe_hub_renewal_and_exact_ready_receipt():
    instance = owner()
    instance.page.evaluate.side_effect = [
        {"schema": "ananta.meet-avatar-sequence-probe.v1", "profile": "persona-sequence-v1", "supported": True},
        {"schema": "ananta.meet-avatar-sequence-preparation.v1", "id": 1},
        {"schema": "ananta.meet-avatar-sequence-status.v1", "id": 1, "state": "ready"}]
    adapter = NativeCompanionSequence(instance)
    assert adapter.prepare({"synthetic": True}, cooperate=Mock()) == 1
    instance.renew.assert_called_once()


def test_hub_denial_prevents_any_native_preparation():
    instance = owner()
    instance.page.evaluate.return_value = {
        "schema": "ananta.meet-avatar-sequence-probe.v1", "profile": "persona-sequence-v1", "supported": True}
    instance.renew.side_effect = ValueError("synthetic_hub_denial")
    with pytest.raises(ValueError, match="synthetic_hub_denial"):
        NativeCompanionSequence(instance).prepare({}, cooperate=Mock())
    assert instance.page.evaluate.call_count == 1


@pytest.mark.parametrize("invalid", ["unsupported", "overbroad", "unregistered_handle"])
def test_invalid_native_contract_cannot_select_legacy_fallback(invalid):
    instance = owner()
    probe = {"schema": "ananta.meet-avatar-sequence-probe.v1", "profile": "persona-sequence-v1", "supported": True}
    if invalid == "unsupported":
        probe["supported"] = False
    elif invalid == "overbroad":
        probe["approval"] = True
    instance.page.evaluate.side_effect = [probe, {"schema": "ananta.meet-avatar-sequence-preparation.v1", "id": True}]
    with pytest.raises(ValueError, match="native_sequence"):
        NativeCompanionSequence(instance).prepare({}, cooperate=Mock())


def test_cancel_releases_pending_preparation_and_local_active_marker_even_when_browser_fails():
    instance = owner()
    instance.sequence_active = True
    instance.avatar_generation = 7
    instance.page.evaluate.side_effect = ValueError("synthetic_browser_closed")
    adapter = NativeCompanionSequence(instance)
    adapter.id = 1
    with pytest.raises(ValueError, match="synthetic_browser_closed"):
        adapter.cancel()
    assert adapter.id is None and instance.sequence_active is False
    assert instance.page.evaluate.call_count == 2
