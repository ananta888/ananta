"""Independent source revisions. A UI change never mints a new capability."""

from dataclasses import dataclass, asdict
from agent.services.meet_contract import MeetError


@dataclass(frozen=True)
class SourceControl:
    enabled: bool
    revision: int
    since: int


@dataclass(frozen=True)
class DialogControls:
    revision: int
    chat: SourceControl
    audio: SourceControl
    screen: SourceControl


def parse_controls(value):
    from ananta_contracts.meet_dialog import validate_controls
    try:
        validate_controls(value)
    except ValueError:
        raise MeetError("meet_dialog_controls_invalid", 403) from None
    return DialogControls(value["revision"], **{name: SourceControl(**value[name]) for name in ("chat", "audio", "screen")})


def initial_controls(capabilities, chat_mode, audio_mode, now_ms):
    return asdict(DialogControls(1,
        SourceControl({"chat.read", "chat.send"} <= set(capabilities) and chat_mode != "off", 1, now_ms),
        SourceControl(audio_mode != "off", 1, now_ms), SourceControl("screen.publish" in capabilities, 1, now_ms)))


def change_controls(scope, payload, now_ms):
    if (not isinstance(payload, dict) or set(payload) != {"expected_revision", "chat", "audio", "screen"}
            or type(payload["expected_revision"]) is not int or any(type(payload[k]) is not bool for k in ("chat", "audio", "screen"))):
        raise MeetError("meet_dialog_controls_invalid")
    current = scope.controls
    if payload["expected_revision"] != current.revision or current.revision >= 1023:
        raise MeetError("meet_dialog_controls_conflict", 409)
    allowed = {"chat": {"chat.read", "chat.send"} <= set(scope.capabilities) and scope.chat_mode != "off",
               "audio": "audio.receive" in scope.capabilities and scope.audio_mode != "off", "screen": "screen.publish" in scope.capabilities}
    if any(payload[name] and not allowed[name] for name in allowed):
        raise MeetError("meet_dialog_control_capability_denied", 403)
    sources = {}
    for name in allowed:
        old = getattr(current, name)
        sources[name] = old if old.enabled == payload[name] else SourceControl(payload[name], old.revision + 1, now_ms)
    return asdict(DialogControls(current.revision + 1, **sources))


def chat_policy_revision(meet_revision, control_revision):
    revision = meet_revision * 1024 + control_revision
    if not 1 <= revision < 2**53:
        raise MeetError("meet_dialog_revision_exhausted", 409)
    return revision
