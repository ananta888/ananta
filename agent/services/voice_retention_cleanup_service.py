from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from agent.common.audit import log_audit
from agent.repositories.voice_governance import VoicePersonalizationRepository
from agent.repositories.voice_result_artifact import VoiceResultArtifactRepository


class ExpiredResultArtifactStore(Protocol):
    def purge_expired(self, *, now: float | None = None) -> int: ...


class ExpiredFeedbackStore(Protocol):
    def purge_all_expired(self, *, now: float | None = None) -> int: ...


class VoiceRetentionCleanupService:
    """Hub-owned physical retention GC with content-free audit evidence."""

    def __init__(
        self,
        *,
        artifacts: ExpiredResultArtifactStore | None = None,
        feedback: ExpiredFeedbackStore | None = None,
        clock: Callable[[], float] = time.time,
        audit_sink: Callable[[str, dict], None] = log_audit,
    ) -> None:
        self._artifacts = artifacts or VoiceResultArtifactRepository()
        self._feedback = feedback or VoicePersonalizationRepository()
        self._clock = clock
        self._audit = audit_sink

    def run_once(self) -> dict[str, int]:
        cutoff = self._clock()
        counts = {
            "voice_result_artifacts": int(self._artifacts.purge_expired(now=cutoff)),
            "voice_feedback": int(self._feedback.purge_all_expired(now=cutoff)),
        }
        self._audit(
            "voice_retention_cleanup_completed",
            {
                "deleted_count": sum(counts.values()),
                "deleted_by_store": counts,
                "status": "completed",
            },
        )
        return counts


voice_retention_cleanup_service = VoiceRetentionCleanupService()


def get_voice_retention_cleanup_service() -> VoiceRetentionCleanupService:
    return voice_retention_cleanup_service
