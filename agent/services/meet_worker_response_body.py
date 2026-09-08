"""Regular Worker HTTP bodies only; budgets remain owned by the caller."""

from worker.meet_media.persona_http import read_bounded


def read_worker_body(response, *, maximum, deadline):
    # The standalone Worker emits Content-Length, never transfer encodings.
    # Chunked read1 can wait for a full trickling chunk header internally,
    # defeating the shared reader's one-underlying-read deadline checkpoints.
    if response.headers.get("Transfer-Encoding") is not None:
        raise ValueError("meet_worker_transfer_encoding_unsupported")
    return read_bounded(response, maximum=maximum, deadline=deadline)
