"""Closed negotiated voice projection and content digests; no evidence identities."""

import hashlib
import json
import re

from ananta_contracts.meet_speech import validate_speech_profile

VOICE_BINDING_FIELDS = frozenset({"voice_selection_digest", "voice_profile_digest"})


def content_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_voice_projection(value):
    if (
        type(value) is not dict
        or set(value) != {"mode", "state", "speech_revision", "selection_digest", "profile"}
        or value["mode"] not in ("configured-piper-v1", "persona-voice-v1")
        or value["state"] not in ("ready", "paused", "blocked")
        or type(value["speech_revision"]) is not int
        or not 1 <= value["speech_revision"] <= 1023
        or type(value["selection_digest"]) is not str
        or not re.fullmatch(r"[a-f0-9]{64}", value["selection_digest"])
    ):
        raise ValueError("meet_dialog_voice_projection_invalid")
    if value["state"] == "ready":
        profile = validate_speech_profile(value["profile"])
    elif value["profile"] is not None:
        raise ValueError("meet_dialog_voice_projection_invalid")
    else:
        profile = None
    return dict(value, profile=profile)


def voice_binding_fields(projection):
    projection = validate_voice_projection(projection)
    if projection["state"] != "ready":
        raise ValueError("meet_dialog_voice_unavailable")
    return {
        "voice_selection_digest": projection["selection_digest"],
        "voice_profile_digest": content_digest(projection["profile"]),
    }


def validate_voice_binding_fields(binding):
    fields = set(binding) & VOICE_BINDING_FIELDS
    if fields and (
        fields != VOICE_BINDING_FIELDS
        or any(type(binding[key]) is not str or not re.fullmatch(r"[a-f0-9]{64}", binding[key]) for key in fields)
    ):
        raise ValueError("meet_dialog_voice_binding_invalid")
