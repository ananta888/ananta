"""Reconnect policy activation is explicit and does not mutate legacy configuration."""

from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, inspect

from agent.bootstrap.meet_recovery import configured_dialog_recovery
from agent.services.meet_dialog_recovery import MeetDialogRecovery


@pytest.mark.parametrize("value", [None, "0"])
def test_disabled_recovery_has_no_database_or_port_side_effects(monkeypatch, value):
    monkeypatch.delenv("ANANTA_MEET_DIALOG_RECONNECT", raising=False)
    if value is not None:
        monkeypatch.setenv("ANANTA_MEET_DIALOG_RECONNECT", value)
    ports = [Mock() for _ in range(5)]
    assert configured_dialog_recovery(*ports) is None
    assert not any(port.mock_calls for port in ports)


@pytest.mark.parametrize("value", ["true", "false", "", "2", "1 "])
def test_malformed_activation_never_silently_enables_recovery(monkeypatch, value):
    monkeypatch.setenv("ANANTA_MEET_DIALOG_RECONNECT", value)
    ports = [Mock() for _ in range(5)]
    with pytest.raises(ValueError, match="config_invalid"):
        configured_dialog_recovery(*ports)
    assert not any(port.mock_calls for port in ports)


@pytest.mark.parametrize("missing", range(1, 5))
def test_missing_authority_retirement_issuer_or_phase_port_fails_before_database_writes(monkeypatch, missing):
    monkeypatch.setenv("ANANTA_MEET_DIALOG_RECONNECT", "1")
    ports = [Mock() for _ in range(5)]
    ports[missing] = None
    with pytest.raises(ValueError, match="coordinators_required"):
        configured_dialog_recovery(*ports)
    assert not ports[0].mock_calls


def test_explicit_activation_uses_one_sql_resource_and_exact_injected_hub_ports(monkeypatch, tmp_path):
    monkeypatch.setenv("ANANTA_MEET_DIALOG_RECONNECT", "1")
    engine = create_engine(f"sqlite:///{tmp_path / 'recovery-bootstrap.sqlite'}")
    authority, meet, issuer, phases, speaker = [Mock() for _ in range(5)]
    try:
        service = configured_dialog_recovery(engine, authority, meet, issuer, phases, speaker_floor=speaker)
        assert isinstance(service, MeetDialogRecovery)
        assert service.authority is authority and service.meet is meet and service.issuer is issuer
        assert service.phases is phases and service.speaker_floor is speaker
        assert service.states.engine is engine
        assert inspect(engine).get_table_names() == ["meet_dialog_recoveries"]
        assert not any(port.mock_calls for port in (authority, meet, issuer, phases, speaker))
    finally:
        engine.dispose()
