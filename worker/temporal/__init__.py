"""Optional Temporal worker runtime.

Importing this package never imports the optional ``temporalio`` dependency.
SDK-backed definitions live in :mod:`worker.temporal.workflows`,
:mod:`worker.temporal.activities` and :mod:`worker.temporal.runtime` and are
loaded only by the dedicated Temporal worker process.
"""

from .config import TemporalWorkerConfig, TemporalWorkerConfigError
from .retry_profiles import ActivityRetryProfile, retry_profile_for

__all__ = [
    "ActivityRetryProfile",
    "TemporalWorkerConfig",
    "TemporalWorkerConfigError",
    "retry_profile_for",
]
