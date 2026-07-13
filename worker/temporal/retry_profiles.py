"""Bounded retry/timeout profiles for Temporal Activities.

The pure profile declarations are importable without the Temporal SDK.  SDK
objects are materialized only by :meth:`ActivityRetryProfile.temporal_options`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ananta_contracts.temporal_workflow import ActivityClass


@dataclass(frozen=True)
class ActivityRetryProfile:
    activity_class: ActivityClass
    schedule_to_close_seconds: int
    start_to_close_seconds: int
    heartbeat_seconds: int | None
    initial_interval_seconds: int
    maximum_interval_seconds: int
    backoff_coefficient: float
    maximum_attempts: int

    def validate(self) -> None:
        if not 1 <= self.maximum_attempts <= 20:
            raise ValueError("Temporal retry attempts must be finite and between 1 and 20")
        if not 1 <= self.initial_interval_seconds <= self.maximum_interval_seconds <= 3_600:
            raise ValueError("Temporal retry intervals are invalid")
        if not 1.0 <= self.backoff_coefficient <= 10.0:
            raise ValueError("Temporal retry backoff is invalid")
        if not 1 <= self.start_to_close_seconds <= self.schedule_to_close_seconds <= 86_400:
            raise ValueError("Temporal activity timeouts are invalid")
        if self.heartbeat_seconds is not None:
            if not 1 <= self.heartbeat_seconds < self.start_to_close_seconds:
                raise ValueError("Temporal heartbeat timeout is invalid")
        if self.activity_class is ActivityClass.NON_IDEMPOTENT and self.maximum_attempts != 1:
            raise ValueError("non-idempotent Activities may not be retried blindly")
        if self.activity_class is ActivityClass.LONG_RUNNING and self.heartbeat_seconds is None:
            raise ValueError("long-running Activities require a heartbeat")

    def temporal_options(self, *, retry_budget_remaining: int) -> dict[str, object]:
        """Return bounded SDK options constrained by the hub-owned budget.

        The workflow passes its remaining budget into this method.  The Activity
        gateway revalidates the budget token at the hub, so this local limit is
        a second, deterministic guard and not an authority source.
        """

        self.validate()
        from temporalio.common import RetryPolicy
        from temporalio.workflow import ActivityCancellationType

        budget_attempts = max(1, min(int(retry_budget_remaining) + 1, self.maximum_attempts))
        if self.activity_class is ActivityClass.NON_IDEMPOTENT:
            budget_attempts = 1
        options: dict[str, object] = {
            "schedule_to_close_timeout": timedelta(seconds=self.schedule_to_close_seconds),
            "start_to_close_timeout": timedelta(seconds=self.start_to_close_seconds),
            "retry_policy": RetryPolicy(
                initial_interval=timedelta(seconds=self.initial_interval_seconds),
                backoff_coefficient=self.backoff_coefficient,
                maximum_interval=timedelta(seconds=self.maximum_interval_seconds),
                maximum_attempts=budget_attempts,
            ),
            # The workflow does not report cancellation as complete until the
            # Activity has acknowledged it and forwarded the request to the
            # hub-owned task. All timeouts remain finite, so this cannot create
            # an unbounded wait.
            "cancellation_type": ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        }
        if self.heartbeat_seconds is not None:
            options["heartbeat_timeout"] = timedelta(seconds=self.heartbeat_seconds)
        return options


_PROFILES = {
    ActivityClass.READ_ONLY: ActivityRetryProfile(
        activity_class=ActivityClass.READ_ONLY,
        schedule_to_close_seconds=120,
        start_to_close_seconds=30,
        heartbeat_seconds=None,
        initial_interval_seconds=1,
        maximum_interval_seconds=10,
        backoff_coefficient=2.0,
        maximum_attempts=5,
    ),
    ActivityClass.IDEMPOTENT: ActivityRetryProfile(
        activity_class=ActivityClass.IDEMPOTENT,
        schedule_to_close_seconds=600,
        start_to_close_seconds=300,
        heartbeat_seconds=30,
        initial_interval_seconds=2,
        maximum_interval_seconds=30,
        backoff_coefficient=2.0,
        maximum_attempts=3,
    ),
    ActivityClass.NON_IDEMPOTENT: ActivityRetryProfile(
        activity_class=ActivityClass.NON_IDEMPOTENT,
        schedule_to_close_seconds=600,
        start_to_close_seconds=300,
        heartbeat_seconds=30,
        initial_interval_seconds=1,
        maximum_interval_seconds=1,
        backoff_coefficient=1.0,
        maximum_attempts=1,
    ),
    ActivityClass.LONG_RUNNING: ActivityRetryProfile(
        activity_class=ActivityClass.LONG_RUNNING,
        schedule_to_close_seconds=86_400,
        start_to_close_seconds=3_600,
        heartbeat_seconds=20,
        initial_interval_seconds=5,
        maximum_interval_seconds=60,
        backoff_coefficient=2.0,
        maximum_attempts=3,
    ),
}


def retry_profile_for(activity_class: ActivityClass | str) -> ActivityRetryProfile:
    resolved = activity_class if isinstance(activity_class, ActivityClass) else ActivityClass(str(activity_class))
    profile = _PROFILES[resolved]
    profile.validate()
    return profile


def validate_all_retry_profiles() -> None:
    for profile in _PROFILES.values():
        profile.validate()


def redacted_heartbeat_details(
    *, operation_id: str, hub_task_id: str, checkpoint_ref: str = "", stage: str = "waiting_for_hub"
) -> dict[str, str]:
    """Heartbeat data contains resumable references, never task or model content."""

    return {
        "schema": "ananta.temporal-heartbeat.v1",
        "operation_id": str(operation_id or "")[:256],
        "hub_task_id": str(hub_task_id or "")[:256],
        "checkpoint_ref": str(checkpoint_ref or "")[:512],
        "stage": str(stage or "waiting_for_hub")[:128],
    }


__all__ = [
    "ActivityRetryProfile",
    "redacted_heartbeat_details",
    "retry_profile_for",
    "validate_all_retry_profiles",
]
