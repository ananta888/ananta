"""Chat content and arbitrary runtime state never enter public diagnostics."""

import pytest

from worker.meet_media.companion_chat_diagnostics import chat_event_diagnostic, chat_state_diagnostic


@pytest.mark.parametrize("kind", ["RECV", "REPLY"])
def test_event_diagnostics_project_only_validated_short_id(kind):
    event = {"message_id": "a" * 32, "text": "private", "sender_peer_id": "private"}
    assert chat_event_diagnostic(kind, event) == f"{kind} id=aaaaaaaa"
    assert chat_event_diagnostic(kind, {"message_id": "private\ninjected"}) == f"{kind} id=invalid"


def test_state_diagnostic_drops_nested_chat_peer_and_encryption_payloads():
    assert chat_state_diagnostic({"peers": [{"name": "private"}], "chat": [{"text": "private"}],
                                  "e2ee": {"secret": "private"}}) == "STATE peers=1 e2ee=False chat=1"
    assert chat_state_diagnostic({"peers": -1, "chat": "private", "e2ee": True}) == "STATE peers=0 e2ee=True chat=0"


def test_unknown_event_kind_cannot_inject_log_record():
    with pytest.raises(ValueError, match="chat_diagnostic_kind_invalid"):
        chat_event_diagnostic("private\nSTATE", {})
