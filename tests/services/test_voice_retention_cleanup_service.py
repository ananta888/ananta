from __future__ import annotations

from agent.services.voice_retention_cleanup_service import VoiceRetentionCleanupService


class _Artifacts:
    def __init__(self) -> None:
        self.cutoff = None

    def purge_expired(self, *, now=None) -> int:
        self.cutoff = now
        return 3


class _Feedback:
    def __init__(self) -> None:
        self.cutoff = None

    def purge_all_expired(self, *, now=None) -> int:
        self.cutoff = now
        return 2


def test_retention_cleanup_physically_purges_all_content_stores_with_one_cutoff() -> None:
    artifacts = _Artifacts()
    feedback = _Feedback()
    audit: list[tuple[str, dict]] = []
    service = VoiceRetentionCleanupService(
        artifacts=artifacts,
        feedback=feedback,
        clock=lambda: 1234.5,
        audit_sink=lambda action, details: audit.append((action, details)),
    )

    result = service.run_once()

    assert result == {"voice_result_artifacts": 3, "voice_feedback": 2}
    assert artifacts.cutoff == feedback.cutoff == 1234.5
    assert audit == [
        (
            "voice_retention_cleanup_completed",
            {
                "deleted_count": 5,
                "deleted_by_store": result,
                "status": "completed",
            },
        )
    ]
