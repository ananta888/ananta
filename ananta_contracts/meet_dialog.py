"""Closed Hub-delegated dialog envelopes; signatures bind direction and request."""

import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit

from ananta_contracts.meet_audio_policy import audio_mode_permitted
from ananta_contracts.meet_audio_profile import parse_audio_profile
from ananta_contracts.meet_initial_persona import validate_initial_persona
from ananta_contracts.meet_source_profile import CAPABILITIES as CAPABILITIES

MAX_DIALOG_BYTES = 16384
ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
OPTIONAL_CONTROL_CAPABILITIES = {"speech": "speech.publish", "avatar": "avatar.publish"}


def validate_controls(value):
    if (
        not isinstance(value, dict)
        or set(value) - OPTIONAL_CONTROL_CAPABILITIES.keys() != {"revision", "chat", "audio", "screen"}
        or type(value["revision"]) is not int
        or not 1 <= value["revision"] <= 1023
    ):
        raise ValueError("meet_dialog_controls_invalid")
    for name in value.keys() - {"revision"}:
        row = value[name]
        if (
            not isinstance(row, dict)
            or set(row) != {"enabled", "revision", "since"}
            or type(row["enabled"]) is not bool
            or type(row["revision"]) is not int
            or not 1 <= row["revision"] <= value["revision"]
            or type(row["since"]) is not int
            or not 1 <= row["since"] < 2**53
        ):
            raise ValueError("meet_dialog_controls_invalid")
    return value


def parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("meet_dialog_duplicate_field")
            value[key] = item
        return value

    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DIALOG_BYTES:
        raise ValueError("meet_dialog_payload_invalid")
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("meet_dialog_number_invalid")),
        )
    except (UnicodeError, RecursionError):
        raise ValueError("meet_dialog_payload_invalid") from None


def _ids(value, names):
    if any(not isinstance(value.get(name), str) or not ID.fullmatch(value[name]) for name in names):
        raise ValueError("meet_dialog_scope_invalid")


def validate_assignment(value, now):
    fields = {
        "schema",
        "task_id",
        "lease_id",
        "runtime_id",
        "session_id",
        "tenant_id",
        "project_id",
        "deadline",
        "capabilities",
        "meeting",
        "audio_mode",
    }
    if (
        not isinstance(value, dict)
        or set(value)
        - {"avatar_images", "avatar_videos", "voice_profiles", "initial_persona", "browser_workspace", "audio_profile"}
        != fields
        or value["schema"] != "ananta.meet-dialog-assignment.v1"
    ):
        raise ValueError("meet_dialog_assignment_invalid")
    _ids(value, fields - {"schema", "deadline", "capabilities", "meeting", "audio_mode"})
    caps = value["capabilities"]
    if (
        not isinstance(caps, list)
        or not caps
        or any(not isinstance(v, str) for v in caps)
        or len(caps) != len(set(caps))
        or not set(caps) <= CAPABILITIES
        or type(value["deadline"]) is not int
        or not now < value["deadline"] <= now + 7200
    ):
        raise ValueError("meet_dialog_assignment_invalid")
    if "avatar_images" in value and (value["avatar_images"] is not True or "avatar.publish" not in caps):
        raise ValueError("meet_dialog_avatar_images_invalid")
    if "avatar_videos" in value and (value["avatar_videos"] is not True or value.get("avatar_images") is not True):
        raise ValueError("meet_dialog_avatar_videos_invalid")
    if "voice_profiles" in value and (value["voice_profiles"] is not True or "speech.publish" not in caps):
        raise ValueError("meet_dialog_voice_profiles_invalid")
    if "browser_workspace" in value and (value["browser_workspace"] is not True or "screen.publish" not in caps):
        raise ValueError("meet_dialog_browser_workspace_invalid")
    if "initial_persona" in value:
        validate_initial_persona(
            value["initial_persona"],
            value["tenant_id"],
            value["project_id"],
            avatar_images=value.get("avatar_images", False),
            avatar_videos=value.get("avatar_videos", False),
            voice_profiles=value.get("voice_profiles", False),
        )
    if not audio_mode_permitted(value["audio_mode"], caps):
        raise ValueError("meet_dialog_audio_policy_invalid")
    if "audio_profile" in value:
        parse_audio_profile(value["audio_profile"])
        if value["audio_mode"] == "off":
            raise ValueError("meet_dialog_audio_policy_invalid")
    meeting = value["meeting"]
    if not isinstance(meeting, dict) or set(meeting) != {"origin", "room_id", "grant"}:
        raise ValueError("meet_dialog_meeting_invalid")
    if any(not isinstance(v, str) for v in meeting.values()):
        raise ValueError("meet_dialog_meeting_invalid")
    url = urlsplit(meeting["origin"])
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.port
        or url.path
        or url.query
        or url.fragment
        or url.netloc != url.hostname
        or not re.fullmatch(r"room-[a-f0-9]{18}", meeting["room_id"])
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,4096}", meeting["grant"])
    ):
        raise ValueError("meet_dialog_meeting_invalid")
    return value


def validate_callback(value, now):
    common = {"schema", "action", "task_id", "lease_id", "runtime_id", "nonce", "sent_at"}
    if not isinstance(value, dict) or value.get("schema") != "ananta.meet-dialog-callback.v1":
        raise ValueError("meet_dialog_callback_invalid")
    action = value.get("action")
    if not isinstance(action, str) or action not in {
        "exchange",
        "chat",
        "finish",
        "audio",
        "transcript",
        "browser_finish",
    }:
        raise ValueError("meet_dialog_callback_invalid")
    fields = (
        {"status"}
        if action in {"finish", "browser_finish"}
        else {"meet_session_id"} | ({"event"} if action == "chat" else set())
    )
    if action == "browser_finish":
        fields |= {"browser_task_id", "browser_lease_id"}
    if action == "audio":
        fields |= {"publication_id"}
    if action == "transcript":
        fields |= {"audio_task_id", "audio_lease_id", "end_sample", "language", "text"}
    if set(value) != common | fields:
        raise ValueError("meet_dialog_callback_invalid")
    _ids(value, ("task_id", "lease_id", "runtime_id"))
    if (
        not isinstance(value["nonce"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"])
        or type(value["sent_at"]) is not int
        or not now - 10 <= value["sent_at"] <= now + 2
    ):
        raise ValueError("meet_dialog_callback_expired")
    if action in {"finish", "browser_finish"}:
        if not isinstance(value["status"], str) or value["status"] not in {"completed", "failed", "cancelled"}:
            raise ValueError("meet_dialog_terminal_invalid")
        if action == "browser_finish":
            _ids(value, ("browser_task_id", "browser_lease_id"))
    elif not isinstance(value["meet_session_id"], str) or not re.fullmatch(
        r"ms_[A-Za-z0-9_-]{32}", value["meet_session_id"]
    ):
        raise ValueError("meet_dialog_session_invalid")
    if action == "chat" and not isinstance(value["event"], dict):
        raise ValueError("meet_dialog_event_invalid")
    if action == "audio":
        from ananta_contracts.meet_dialog_audio import PUBLICATION_ID

        if not isinstance(value["publication_id"], str) or not PUBLICATION_ID.fullmatch(value["publication_id"]):
            raise ValueError("meet_dialog_publication_invalid")
    if action == "transcript":
        _ids(value, ("audio_task_id", "audio_lease_id"))
        if (
            type(value["end_sample"]) is not int
            or not 16000 <= value["end_sample"] <= 160000
            or value["end_sample"] % 1600 != 0
            or not isinstance(value["language"], str)
            or value["language"] not in {"de", "en"}
            or not isinstance(value["text"], str)
            or len(value["text"]) > 2000
        ):
            raise ValueError("meet_dialog_transcript_invalid")
        try:
            if len(value["text"].encode("utf-8")) > 4000 or any(ord(c) < 32 and c not in "\n\t" for c in value["text"]):
                raise ValueError("meet_dialog_transcript_invalid")
        except UnicodeError:
            raise ValueError("meet_dialog_transcript_invalid") from None
    return value


def request_signature(key, body):
    return hmac.new(key, b"meet-dialog-request-v1\0" + body, hashlib.sha256).hexdigest()


def response_signature(key, request_body, response_body):
    return hmac.new(
        key, b"meet-dialog-response-v1\0" + hashlib.sha256(request_body).digest() + response_body, hashlib.sha256
    ).hexdigest()
