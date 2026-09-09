"""Speaker enforcement is explicit Hub composition, not a Worker policy flag."""

from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, inspect

from agent.bootstrap.meet_speaker import configured_speaker_floor
from agent.services.meet_dialog_speaker_floor import MeetDialogSpeakerFloor


@pytest.mark.parametrize("value", [None, "0"])
def test_disabled_speaker_policy_has_no_database_side_effect(monkeypatch, value):
    monkeypatch.delenv("ANANTA_MEET_SPEAKER_FLOOR", raising=False)
    if value is not None:
        monkeypatch.setenv("ANANTA_MEET_SPEAKER_FLOOR", value)
    engine = Mock()
    assert configured_speaker_floor(engine) is None
    assert not engine.mock_calls


@pytest.mark.parametrize("value", ["true", "false", "", "2", "1 "])
def test_malformed_activation_is_not_silently_accepted(monkeypatch, value):
    monkeypatch.setenv("ANANTA_MEET_SPEAKER_FLOOR", value)
    with pytest.raises(ValueError, match="config_invalid"):
        configured_speaker_floor(Mock())


def test_explicit_bootstrap_wires_same_durable_resource_into_both_service_ports(monkeypatch, tmp_path):
    monkeypatch.setenv("ANANTA_MEET_SPEAKER_FLOOR", "1")
    engine = create_engine(f"sqlite:///{tmp_path / 'speaker-config.sqlite'}")
    try:
        service = configured_speaker_floor(engine)
        assert isinstance(service, MeetDialogSpeakerFloor)
        assert service.admission.store is service.states
        assert set(inspect(engine).get_table_names()) == {"meet_speaker_floor_rooms", "meet_speaker_floor_turns"}
        assert service.ready_at > service.monotonic()
    finally:
        engine.dispose()
