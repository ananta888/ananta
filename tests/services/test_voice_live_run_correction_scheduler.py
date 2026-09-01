from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_live_run_correction_service import VoiceLiveRunCorrectionService

ROOT = Path(__file__).resolve().parents[2]


def test_publication_audit_precedes_asynchronous_correction_schedule() -> None:
    source = (ROOT / "agent/routes/voice_live_runs.py").read_text(encoding="utf-8")
    publication = source.index('"voice_live_run_segment_provisional_published"')
    schedule = source.index("correction_service.schedule(principal, run_id, sequence)", publication)

    assert publication < schedule


def test_bounded_backlog_runs_automatically_after_worker_capacity_returns() -> None:
    started = threading.Event()
    release = threading.Event()
    executed: list[int] = []
    principal = VoicePrincipal(tenant_id="tenant", subject="operator")

    with ThreadPoolExecutor(max_workers=1) as executor:
        service = VoiceLiveRunCorrectionService(
            repository=Mock(),
            artifacts=Mock(),
            tasks=Mock(),
            tombstones=Mock(),
            postprocessing=Mock(),
            executor=executor,
            max_outstanding_corrections=3,
        )

        def execute(_principal: VoicePrincipal, _run_id: str, sequence: int) -> None:
            if sequence == 0:
                started.set()
                assert release.wait(timeout=5)
            executed.append(sequence)

        service._claim_and_execute = execute  # type: ignore[method-assign]

        assert service.schedule(principal, "run", 0)
        assert started.wait(timeout=5)
        assert service.schedule(principal, "run", 1)
        assert service.schedule(principal, "run", 2)
        assert not service.schedule(principal, "run", 3)

        release.set()
        assert service.wait_for_idle(timeout=5)

    assert executed == [0, 1, 2]
