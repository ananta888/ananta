"""Closed speaker permit projection; parsing never grants room authority."""

import re


def validate_speaker_permit(value, *, deadline_ms=None):
    if (
        not isinstance(value, dict)
        or set(value) != {"id", "sequence", "expires_ms"}
        or not isinstance(value["id"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["id"])
        or any(type(value[k]) is not int or not 0 < value[k] < 2**53 for k in ("sequence", "expires_ms"))
        or deadline_ms is not None
        and (type(deadline_ms) is not int or not 0 < value["expires_ms"] <= deadline_ms < 2**53)
    ):
        raise ValueError("meet_speaker_permit_invalid")
    return dict(value)
