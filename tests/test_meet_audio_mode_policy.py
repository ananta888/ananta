"""Synthetic policy matrix; no production receive grant or media is used."""

from itertools import combinations

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_controls import initial_controls
from ananta_contracts.meet_audio_policy import audio_mode_permitted
from ananta_contracts.meet_dialog import validate_assignment
from tests.test_meet_dialog_audio import runtime
from tests.test_meet_dialog_authority import fixture
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_dialog_transport import assignment

CAPABILITIES = ("audio.receive", "chat.read", "chat.send", "speech.publish")
CAPABILITY_SETS = [list(c) for n in range(1, 5) for c in combinations(CAPABILITIES, n)]


@pytest.mark.parametrize("caps", CAPABILITY_SETS)
@pytest.mark.parametrize("mode", ["off", "transcribe", "dialog", "record", None, [], True])
def test_closed_worker_assignment_has_only_mode_required_rights(caps, mode):
    expected = mode == "off" or (
        mode in ("transcribe", "dialog") and "audio.receive" in caps and (mode == "transcribe" or "chat.send" in caps)
    )
    assert audio_mode_permitted(mode, caps) is expected
    wire = assignment() | {"audio_mode": mode, "capabilities": caps}
    if expected:
        assert validate_assignment(wire, wire["deadline"] - 600) == wire
    else:
        with pytest.raises(ValueError, match="audio_policy_invalid"):
            validate_assignment(wire, wire["deadline"] - 600)


@pytest.mark.parametrize(
    "mode,caps,chat,allowed",
    [
        ("transcribe", ["audio.receive"], "off", True),
        ("transcribe", ["chat.send"], "off", False),
        ("dialog", ["audio.receive"], "mention", False),
        ("dialog", ["audio.receive", "chat.send"], "mention", True),
        ("dialog", ["audio.receive", "chat.send"], "off", False),
    ],
)
def test_actual_hub_task_and_persisted_authority_enforce_same_mode(app, mode, caps, chat, allowed):
    with app.app_context():
        f = system(profiles=False)
        f.f.authority.policies[("tenant", "project")] = frozenset(CAPABILITIES)
        request = f.payload | {"audio_mode": mode, "capabilities": caps, "chat_mode": chat}
        if not allowed:
            with pytest.raises(MeetError, match="audio_policy_denied"):
                f.service.start(f.principal, "project", request)
            f.worker.start_dialog.assert_not_called()
            return
        result = f.service.start(f.principal, "project", request)
        wire = f.worker.start_dialog.call_args.args[0]
        assert validate_assignment(wire, f.f.now) == wire
        assert wire["capabilities"] == sorted(caps)
        row = f.tasks.get_by_id(result["task_id"])
        context = row.worker_execution_context["meet_dialog"]
        assert context["controls"]["audio"]["enabled"] is True
        assert context["controls"]["chat"]["enabled"] is False


def test_receive_only_child_completes_without_reply_or_transcript_persistence():
    f, receipt, service, payload = runtime()
    f.context.update(capabilities=["audio.receive"], chat_mode="off")
    f.context["controls"] = initial_controls(["audio.receive"], "off", "transcribe", f.now * 1000)
    job = service.start(payload)["job"]
    result = service.complete(
        payload
        | {
            "audio_task_id": job["task_id"],
            "audio_lease_id": job["lease_id"],
            "end_sample": 160000,
            "language": "de",
            "text": "synthetic ephemeral content",
        }
    )
    assert result["reply"] is None
    assert "synthetic ephemeral content" not in str(f.tasks.finish_audio.call_args)
    service.media_worker.execute.assert_not_called()
    f.authority.policies[("tenant", "project")] = frozenset()
    with pytest.raises(MeetError):
        service.current(("task", "dispatch", "runtime"), job)


@pytest.mark.parametrize("mutation", ["remove_send", "remove_receive", "disable_reply"])
def test_persisted_dialog_cannot_keep_running_after_required_rights_change(mutation):
    f = fixture()
    f.context["audio_mode"] = "dialog"
    f.authority.current("task", "dispatch", "runtime")
    if mutation == "disable_reply":
        f.context["chat_mode"] = "off"
    else:
        f.context["capabilities"] = [
            c
            for c in f.context["capabilities"]
            if c
            != {
                "remove_send": "chat.send",
                "remove_receive": "audio.receive",
            }[mutation]
        ]
    with pytest.raises(MeetError, match="policy_denied"):
        f.authority.current("task", "dispatch", "runtime")
