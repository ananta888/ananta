"""Regular Worker HTTP bodies only; budgets remain owned by the caller."""

from worker.meet_media.persona_http import read_bounded


def _response_length(headers, maximum):
    # HTTPMessage preserves duplicate fields. Plain mappings remain useful for
    # injected transports/tests, but must not gain a different framing policy.
    get_all = getattr(headers, "get_all", None)
    values = get_all("Content-Length", []) if callable(get_all) else [headers.get("Content-Length")]
    if not values or values == [None]:
        return None
    if len(values) != 1 or not isinstance(values[0], str):
        raise ValueError("meet_worker_content_length_invalid")
    value = values[0].strip(" \t")
    if not 1 <= len(value) <= 20 or not value.isascii() or not value.isdecimal():
        raise ValueError("meet_worker_content_length_invalid")
    length = int(value)
    if length > maximum:
        raise ValueError("persona_http_body_too_large")
    return length


def read_worker_body(response, *, maximum, deadline):
    # The standalone Worker emits Content-Length, never transfer encodings.
    # Chunked read1 can wait for a full trickling chunk header internally,
    # defeating the shared reader's one-underlying-read deadline checkpoints.
    if response.headers.get("Transfer-Encoding") is not None:
        raise ValueError("meet_worker_transfer_encoding_unsupported")
    return read_bounded(
        response, maximum=maximum, deadline=deadline, length=_response_length(response.headers, maximum)
    )
