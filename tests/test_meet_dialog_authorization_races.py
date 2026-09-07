"""Backchannel membership stays independent of source-selection CAS."""

import io
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent.services.meet_authorization_client import MeetAuthorizationClient
from agent.services.meet_contract import MeetError
from tests.test_meet_dialog_avatar_controls import avatar_scope
from tests.test_meet_dialog_avatar_selection import image_selection


@pytest.mark.parametrize("change", ["image", "controls", "owner", "runtime", "deadline", "capabilities", "negotiation"])
def test_concurrent_source_update_is_not_parent_revocation_but_identity_changes_are(monkeypatch, change):
    f, scope = avatar_scope()
    scope = replace(scope, avatar_selection={"mode": "neutral-ai-v1"})
    patches = {
        "image": {"avatar_selection": image_selection()},
        "controls": {"controls": replace(scope.controls, revision=scope.controls.revision + 1)},
        "owner": {"owner_subject": "foreign"},
        "runtime": {"runtime_id": "replacement"},
        "deadline": {"deadline": scope.deadline + 1},
        "capabilities": {"capabilities": ()},
        "negotiation": {"avatar_selection": None},
    }
    current = replace(scope, **patches[change])
    authority = Mock()
    authority.current.side_effect = [scope, current]
    issuer = Mock()
    issuer.issue_dialog.return_value = {"grant": "synthetic-fixture-only"}
    opener = Mock()
    opener.open.return_value = io.BytesIO(b"{}")
    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: opener)
    # This test isolates the post-I/O Hub race check; no TLS/Meet verification claim.
    validated = {"validated": "synthetic"}
    monkeypatch.setattr("agent.services.meet_authorization_client.validate_authorization", lambda *_args: validated)
    client = MeetAuthorizationClient(authority, issuer, clock=lambda: f.now)
    if change in {"image", "controls"}:
        assert client.inspect(scope.task_id, scope.lease_id, scope.runtime_id, "ms_" + "a" * 32) == validated
    else:
        with pytest.raises(MeetError, match="authorization_changed"):
            client.inspect(scope.task_id, scope.lease_id, scope.runtime_id, "ms_" + "a" * 32)
