from unittest.mock import patch

import pytest

from agent.services.chat_process_binding import normalize_process_ref, resolve_effective_process


def test_process_reference_is_strict_and_normalized():
    assert normalize_process_ref(None) is None
    assert normalize_process_ref({"graph_id": "vp-1"}) == {"graph_id": "vp-1", "version": "latest"}
    with pytest.raises(ValueError, match="process_graph_id_required"):
        normalize_process_ref({"version": "1.0"})


def test_session_process_overrides_profile_without_mutating_definition():
    session = {"process_ref": {"graph_id": "vp-session", "version": "2"}}
    profile = {"process_ref": {"graph_id": "vp-profile", "version": "1"}}
    graph = {"id": "vp-session", "steps": [{"id": "step-1"}]}
    with patch("agent.services.chat_process_binding.load_graph", return_value=graph):
        result = resolve_effective_process(session, profile)
    assert result["source"] == "session"
    assert result["graph"] == graph
    assert "run_state" not in result["graph"]["steps"][0]


def test_profile_process_is_inherited_when_session_has_no_override():
    profile = {"process_ref": {"graph_id": "vp-profile", "version": "1"}}
    with patch("agent.services.chat_process_binding.load_graph", return_value={"id": "vp-profile"}):
        result = resolve_effective_process({}, profile)
    assert result["source"] == "profile"
    assert result["process_ref"] == profile["process_ref"]
