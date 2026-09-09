"""Independent source revisions. A UI change never mints a new capability."""

from dataclasses import asdict, dataclass

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import OPTIONAL_CONTROL_CAPABILITIES


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
    speech: SourceControl | None = None
    avatar: SourceControl | None = None
    visual: SourceControl | None = None


def controls_projection(value):
    """Existing tasks retain exactly their original three-source wire shape."""
    result = asdict(value)
    for name in OPTIONAL_CONTROL_CAPABILITIES:
        if result[name] is None:
            result.pop(name)
    return result


def parse_controls(value):
    from ananta_contracts.meet_dialog import validate_controls

    try:
        validate_controls(value)
    except ValueError:
        raise MeetError("meet_dialog_controls_invalid", 403) from None
    return DialogControls(
        value["revision"], **{name: SourceControl(**row) for name, row in value.items() if name != "revision"}
    )


def initial_controls(capabilities, chat_mode, audio_mode, now_ms):
    return controls_projection(
        DialogControls(
            1,
            SourceControl({"chat.read", "chat.send"} <= set(capabilities) and chat_mode != "off", 1, now_ms),
            SourceControl(audio_mode != "off", 1, now_ms),
            SourceControl("screen.publish" in capabilities, 1, now_ms),
            SourceControl(chat_mode != "off", 1, now_ms) if "speech.publish" in capabilities else None,
            # Permission is not activation: neutral avatar requires an explicit
            # current Hub control mutation, independently of chat or speech.
            SourceControl(False, 1, now_ms) if "avatar.publish" in capabilities else None,
            SourceControl(False, 1, now_ms) if "video.receive" in capabilities else None,
        )
    )


def change_controls(scope, payload, now_ms):
    if (
        not isinstance(payload, dict)
        or set(payload) - OPTIONAL_CONTROL_CAPABILITIES.keys() != {"expected_revision", "chat", "audio", "screen"}
        or type(payload["expected_revision"]) is not int
        or any(type(payload[k]) is not bool for k in payload if k != "expected_revision")
    ):
        raise MeetError("meet_dialog_controls_invalid")
    current = scope.controls
    if any(name in payload and getattr(current, name) is None for name in OPTIONAL_CONTROL_CAPABILITIES):
        raise MeetError("meet_dialog_control_capability_denied", 403)
    if payload["expected_revision"] != current.revision or current.revision >= 1023:
        raise MeetError("meet_dialog_controls_conflict", 409)
    allowed = {
        "chat": {"chat.read", "chat.send"} <= set(scope.capabilities) and scope.chat_mode != "off",
        "audio": "audio.receive" in scope.capabilities and scope.audio_mode != "off",
        "screen": "screen.publish" in scope.capabilities,
    }
    for name, capability in OPTIONAL_CONTROL_CAPABILITIES.items():
        source = getattr(current, name)
        if source is not None:
            allowed[name] = capability in scope.capabilities and (name != "speech" or scope.chat_mode != "off")
            payload = {name: source.enabled, **payload}
    if any(payload[name] and not allowed[name] for name in allowed):
        raise MeetError("meet_dialog_control_capability_denied", 403)
    sources = {}
    for name in allowed:
        old = getattr(current, name)
        sources[name] = old if old.enabled == payload[name] else SourceControl(payload[name], old.revision + 1, now_ms)
    return controls_projection(DialogControls(current.revision + 1, **sources))


def chat_policy_revision(meet_revision, control_revision):
    revision = meet_revision * 1024 + control_revision
    if not 1 <= revision < 2**53:
        raise MeetError("meet_dialog_revision_exhausted", 409)
    return revision
