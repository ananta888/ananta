"""Closed transient text view, never HTML, a screenshot, or publication authority."""

import re
import unicodedata

SCHEMA = "ananta.browser-public-view.v1"
MAX_BLOCKS = 128
MAX_BLOCK_CHARS = 500
MAX_TEXT_CHARS = 8192
MAX_TEXT_BYTES = 32768
BLOCK_REASONS = frozenset(
    {
        "sensitive_content",
        "active_content",
        "scope_changed",
        "snapshot_too_large",
        "snapshot_invalid",
        "source_unavailable",
        "no_visible_text",
    }
)


def blocked_view(reason):
    if type(reason) is not str or reason not in BLOCK_REASONS:
        raise ValueError("browser_public_view_invalid")
    return {"schema": SCHEMA, "state": "blocked", "reason": reason, "blocks": []}


def validate_public_view(value):
    error = "browser_public_view_invalid"
    if type(value) is not dict or set(value) != {"schema", "state", "reason", "blocks"} or value["schema"] != SCHEMA:
        raise ValueError(error)
    if value["state"] == "blocked":
        if type(value["blocks"]) is not list or value["blocks"] != []:
            raise ValueError(error)
        return blocked_view(value["reason"])
    blocks = value["blocks"]
    if (
        value["state"] != "ready"
        or value["reason"] is not None
        or type(blocks) is not list
        or not 1 <= len(blocks) <= MAX_BLOCKS
    ):
        raise ValueError(error)
    copied, characters, encoded_bytes = [], 0, 0
    for block in blocks:
        if type(block) is not dict or set(block) != {"kind", "text"} or block["kind"] not in ("heading", "text"):
            raise ValueError(error)
        text = block["text"]
        if (
            type(text) is not str
            or not 1 <= len(text) <= MAX_BLOCK_CHARS
            or text.strip() != text
            or unicodedata.normalize("NFC", text) != text
            or re.search(r"[\x00-\x1f\x7f\ud800-\udfff\u202a-\u202e\u2066-\u2069]", text)
        ):
            raise ValueError(error)
        characters += len(text)
        encoded_bytes += len(text.encode("utf-8"))
        if characters > MAX_TEXT_CHARS or encoded_bytes > MAX_TEXT_BYTES:
            raise ValueError(error)
        copied.append(dict(block))
    return {"schema": SCHEMA, "state": "ready", "reason": None, "blocks": copied}
