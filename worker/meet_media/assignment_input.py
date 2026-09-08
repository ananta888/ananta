"""Anonymous bounded startup input, independent of a child's pipe consumption."""

from contextlib import contextmanager
from tempfile import TemporaryFile

from ananta_contracts.meet_dialog import MAX_DIALOG_BYTES


@contextmanager
def dialog_assignment_input(raw, *, file_factory=TemporaryFile):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DIALOG_BYTES:
        raise ValueError("meet_dialog_payload_invalid")
    # No named grant artifact or argv/environment payload. Unbuffered writes
    # finish before spawning; a short write never launches a partial assignment.
    with file_factory(mode="w+b", buffering=0) as source:
        if source.write(raw) != len(raw):
            raise ValueError("meet_dialog_input_incomplete")
        source.seek(0)
        yield source
