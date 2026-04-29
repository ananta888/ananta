from __future__ import annotations

from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader

_reader = CodeCompassOutputReader()


def get_codecompass_output_reader() -> CodeCompassOutputReader:
    return _reader

