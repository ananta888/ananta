"""Isolated LoRA-training worker domain.

This package is deliberately independent from :mod:`agent`: the Hub owns
orchestration while this package only executes an already-admitted job.
"""

from worker.training.contracts import CONTRACT_VERSION

__all__ = ["CONTRACT_VERSION"]
