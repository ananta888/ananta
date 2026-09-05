"""Narrow compatibility helpers for supported TRL releases."""

from __future__ import annotations

import inspect
from typing import Any

from worker.training.backends.base import TrainingBackendError


def sequence_length_options(sft_config_type: Any, value: int) -> dict[str, int]:
    """Return the sequence-length keyword supported by ``SFTConfig``.

    TRL renamed ``max_seq_length`` to ``max_length``.  Detecting the public
    constructor contract keeps both supported runtime generations substitutable
    without coupling the worker backend to a package-version string.
    """

    parameters = inspect.signature(sft_config_type).parameters
    if "max_length" in parameters:
        return {"max_length": value}
    if "max_seq_length" in parameters:
        return {"max_seq_length": value}
    raise TrainingBackendError(
        "dependency_incompatible",
        "installed TRL SFTConfig exposes no supported sequence-length option",
    )
