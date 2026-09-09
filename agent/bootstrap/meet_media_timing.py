"""Explicit Hub execution profile; never inferred from browser availability."""

import os


def configured_media_timing():
    value = os.environ.get("ANANTA_MEET_MEDIA_TIMING", "0")
    if value not in {"0", "1"}:
        raise ValueError("meet_media_timing_config_invalid")
    return value == "1"
