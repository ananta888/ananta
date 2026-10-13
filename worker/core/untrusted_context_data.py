"""Bounded quoted context data, without policy or identity issuance."""

import json
from dataclasses import dataclass

from worker.core.redaction import redact_text


@dataclass(frozen=True)
class QuotedContextData:
    text: str
    truncated: bool


def quote_context_data(content: str, *, max_chars: int, source_type: str = "retrieved_context",
                       origin_id: str = "", provenance: str = "") -> QuotedContextData | None:
    """Fit complete JSON into the budget; references are descriptive only."""
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("context_data_budget_invalid")
    body = redact_text(str(content or ""))
    payload = {
        "schema": "untrusted_context_data.v1", "trust": "untrusted", "authority": "none",
        "source_type": redact_text(str(source_type or ""))[:128],
        "origin_ref": redact_text(str(origin_id or ""))[:256],
        "provenance_ref": redact_text(str(provenance or ""))[:256],
        "identity_verified": False, "content": "", "truncated": False,
    }

    def encode(length):
        payload["content"] = body[:length]
        payload["truncated"] = length < len(body)
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        return f"<untrusted_context_data>\n{encoded}\n</untrusted_context_data>"

    upper = min(len(body), max_chars)
    complete = encode(upper)
    if len(complete) <= max_chars:
        return QuotedContextData(complete, upper < len(body))
    if len(encode(0)) > max_chars:
        return None
    low, high = 0, upper
    while low < high:
        middle = (low + high + 1) // 2
        if len(encode(middle)) <= max_chars:
            low = middle
        else:
            high = middle - 1
    if low == 0 and body:
        return None
    return QuotedContextData(encode(low), low < len(body))
