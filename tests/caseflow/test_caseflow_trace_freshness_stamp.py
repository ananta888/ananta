"""The revision stamp that lets a client tell a fresh projection from a stale one.

Without it a terminal trace read before a run finished is indistinguishable
from one read after, so a canvas would stop polling on a picture of a run that
had not yet ended.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services.caseflow_agent_collaboration_trace_projection_service import (
    CaseflowAgentCollaborationTraceProjectionService,
)


class _Binding:
    tenant_id = "tenant-a"
    workflow_id = "workflow-a"
    run_id = "run-a"
    execution_plan: dict[str, Any] = {}
    request: Any = None


class _Bindings:
    def get(self, workflow_id: str) -> Any:
        return _Binding() if workflow_id == "workflow-a" else None


class _History:
    """History port that also reports authoritative status, like the backend."""

    def __init__(self, *, revision: Any = 8, status_error: Exception | None = None) -> None:
        self.revision = revision
        self.status_error = status_error
        self.status_calls = 0

    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        del workflow_id
        return []

    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        del workflow_id
        self.status_calls += 1
        if self.status_error is not None:
            raise self.status_error
        return {"status": "completed", "revision": self.revision}


class _HistoryWithoutStatus:
    def list_workflow_events(self, workflow_id: str) -> list[dict[str, Any]]:
        del workflow_id
        return []


def _service() -> CaseflowAgentCollaborationTraceProjectionService:
    return CaseflowAgentCollaborationTraceProjectionService(_Bindings())


def test_a_projection_is_stamped_with_the_revision_it_was_built_at() -> None:
    projection = _service().project(binding=_Binding(), raw_events=[], source_revision=11)

    assert projection["source_revision"] == 11


def test_an_unknown_revision_is_omitted_rather_than_guessed() -> None:
    """A fabricated stamp would defeat the staleness check it enables."""

    projection = _service().project(binding=_Binding(), raw_events=[], source_revision=None)

    assert "source_revision" not in projection


@pytest.mark.parametrize("revision", (-1, True, "8", 1.5))
def test_a_malformed_revision_is_never_stamped(revision: Any) -> None:
    projection = _service().project(binding=_Binding(), raw_events=[], source_revision=revision)

    assert "source_revision" not in projection


def test_a_zero_revision_is_a_legitimate_stamp() -> None:
    projection = _service().project(binding=_Binding(), raw_events=[], source_revision=0)

    assert projection["source_revision"] == 0


def test_a_history_port_that_reports_status_stamps_the_projection() -> None:
    history = _History(revision=9)

    projection = _service().project(binding=_Binding(), raw_events=[], source_revision=9)

    assert projection["source_revision"] == 9
    assert history.status_calls == 0


def test_a_history_port_without_status_yields_an_unstamped_projection() -> None:
    from agent.services.caseflow_agent_collaboration_trace_projection_service import (
        _observed_revision,
    )

    assert _observed_revision(_HistoryWithoutStatus(), "workflow-a") is None


def test_an_unavailable_status_read_never_fails_the_trace_it_was_stamping() -> None:
    from agent.services.caseflow_agent_collaboration_trace_projection_service import (
        _observed_revision,
    )

    history = _History(status_error=TimeoutError("status unavailable"))

    assert _observed_revision(history, "workflow-a") is None
    assert history.status_calls == 1


@pytest.mark.parametrize("revision", (-1, True, "8", None))
def test_a_malformed_status_revision_is_read_as_unknown(revision: Any) -> None:
    from agent.services.caseflow_agent_collaboration_trace_projection_service import (
        _observed_revision,
    )

    assert _observed_revision(_History(revision=revision), "workflow-a") is None


def test_the_stamp_survives_the_unverified_catalog_path_too() -> None:
    """Both return paths carry the stamp; the early one must not drop it."""

    projection = _service().project(binding=_Binding(), raw_events=[], source_revision=4)

    assert projection["source_revision"] == 4
    assert projection["catalog_verification_status"] in {"verified", "unverified"}
