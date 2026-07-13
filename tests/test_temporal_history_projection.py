from __future__ import annotations

import asyncio
from pathlib import Path

from sqlmodel import SQLModel, create_engine

from agent.db_models.temporal_runtime import TemporalHistoryProjectionDB, TemporalProjectedEventDB
from agent.services.temporal_history_projection import (
    TEMPORAL_HISTORY_MAPPING_VERSION,
    InMemoryTemporalProjectionRepository,
    SQLTemporalProjectionRepository,
    TemporalHistoryPage,
    TemporalHistoryProjectionService,
    TemporalHistoryRecord,
    TemporalProjectionCursor,
)


def _record(event_id: int, event_type: str, **attributes) -> TemporalHistoryRecord:
    return TemporalHistoryRecord(
        event_id=event_id,
        event_type=event_type,
        occurred_at=float(event_id),
        attributes=dict(attributes),
    )


class PagedSource:
    def __init__(self, pages: dict[str, TemporalHistoryPage]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    async def fetch_page(self, **kwargs) -> TemporalHistoryPage:
        token = str(kwargs["next_page_token"])
        self.calls.append(token)
        return self.pages[token]


def _service(source, repository=None) -> TemporalHistoryProjectionService:
    service = TemporalHistoryProjectionService(
        namespace="default",
        source=source,
        repository=repository or InMemoryTemporalProjectionRepository(),
    )
    service.bind_run(
        tenant_id="tenant-1",
        workflow_id="wf-1",
        run_id="run-1",
        temporal_run_id="temporal-run-1",
        correlation_id="correlation-1",
    )
    return service


def test_projection_paginates_deduplicates_and_maps_activity_steps() -> None:
    source = PagedSource(
        {
            "": TemporalHistoryPage(
                records=(
                    _record(1, "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED", workflow_type="AnantaWorkflow"),
                    _record(
                        2,
                        "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED",
                        activity_id="ananta:step-1:operation-1",
                        activity_type="ananta.hub-task.execute.v1",
                    ),
                ),
                next_page_token="page-2",
            ),
            "page-2": TemporalHistoryPage(
                records=(
                    _record(2, "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"),
                    _record(3, "EVENT_TYPE_ACTIVITY_TASK_STARTED", scheduled_event_id=2, attempt=1),
                    _record(4, "EVENT_TYPE_ACTIVITY_TASK_COMPLETED", scheduled_event_id=2),
                )
            ),
        }
    )
    page = asyncio.run(_service(source).synchronize("wf-1"))

    assert page["consistency_state"] == "current"
    assert page["projection_cursor"] == 4
    assert page["lag"] == 0
    assert len(page["events"]) == 4
    assert page["events"][2]["step_id"] == "step-1"
    assert source.calls == ["", "page-2"]


def test_projection_gap_is_fail_closed_and_never_current() -> None:
    source = PagedSource(
        {
            "": TemporalHistoryPage(
                records=(
                    _record(1, "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"),
                    _record(3, "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"),
                )
            )
        }
    )
    page = asyncio.run(_service(source).synchronize("wf-1"))
    assert page["consistency_state"] == "inconsistent"
    assert page["reason_code"] == "temporal_history_gap"


def test_projection_page_token_loop_is_inconsistent() -> None:
    source = PagedSource(
        {
            "": TemporalHistoryPage(
                records=(_record(1, "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"),),
                next_page_token="same",
            ),
            "same": TemporalHistoryPage(records=(), next_page_token="same"),
        }
    )
    page = asyncio.run(_service(source).synchronize("wf-1"))
    assert page["consistency_state"] == "inconsistent"
    assert page["reason_code"] == "projection_page_token_loop"


def test_projection_mapping_upgrade_requires_explicit_rebuild() -> None:
    repository = InMemoryTemporalProjectionRepository()
    source = PagedSource({"": TemporalHistoryPage(records=(_record(1, "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"),))})
    service = _service(source, repository)
    original = repository.get_cursor(namespace="default", workflow_id="wf-1")
    assert original is not None
    repository._cursors[("default", "wf-1")] = TemporalProjectionCursor(  # noqa: SLF001 - deterministic fixture
        **{**original.__dict__, "mapping_version": "ananta.temporal-history-map.v0"}
    )

    stale = asyncio.run(service.synchronize("wf-1"))
    assert stale["consistency_state"] == "stale"
    assert stale["reason_code"] == "projection_mapping_upgrade_required"
    rebuilt = asyncio.run(service.rebuild("wf-1", expected_tenant_id="tenant-1"))
    assert rebuilt["consistency_state"] == "current"
    assert rebuilt["mapping_version"] == TEMPORAL_HISTORY_MAPPING_VERSION


def test_sql_projection_cursor_and_events_survive_repository_restart(tmp_path: Path) -> None:
    database = tmp_path / "temporal-projection.db"
    engine = create_engine(f"sqlite:///{database}")
    SQLModel.metadata.create_all(
        engine,
        tables=[TemporalHistoryProjectionDB.__table__, TemporalProjectedEventDB.__table__],
    )
    source = PagedSource(
        {
            "": TemporalHistoryPage(
                records=(
                    _record(1, "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"),
                    _record(2, "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"),
                )
            )
        }
    )
    service = _service(source, SQLTemporalProjectionRepository(engine))
    first = asyncio.run(service.synchronize("wf-1", expected_tenant_id="tenant-1"))
    assert first["consistency_state"] == "current"

    restarted_repository = SQLTemporalProjectionRepository(engine)
    cursor = restarted_repository.get_cursor(namespace="default", workflow_id="wf-1")
    assert cursor is not None
    assert cursor.last_event_id == 2
    events = restarted_repository.list_events(projection_id=cursor.projection_id, after_event_id=0, limit=10)
    assert [event["sequence"] for event in events] == [1, 2]


def test_projection_rebuild_is_deterministic() -> None:
    source = PagedSource(
        {
            "": TemporalHistoryPage(
                records=(
                    _record(1, "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"),
                    _record(2, "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"),
                )
            )
        }
    )
    service = _service(source)
    first = asyncio.run(service.synchronize("wf-1", expected_tenant_id="tenant-1"))
    rebuilt = asyncio.run(service.rebuild("wf-1", expected_tenant_id="tenant-1"))
    assert rebuilt["events"] == first["events"]


def test_projection_rejects_cross_tenant_read() -> None:
    source = PagedSource({"": TemporalHistoryPage(records=())})
    page = asyncio.run(_service(source).synchronize("wf-1", expected_tenant_id="tenant-other"))
    assert page["consistency_state"] == "inconsistent"
    assert page["events"] == []
    assert page["reason_code"] == "projection_tenant_mismatch"
