from __future__ import annotations

import asyncio

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.dashboard_autoload import DashboardSectionAutoloader
from client_surfaces.operator_tui.dashboard_live_lifecycle import (
    DashboardLiveSyncLifecycle,
)
from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import PanelState, SectionLoadResult


def _snapshot(sequence: int, *, board_id: str = "hub") -> SectionLoadResult:
    return SectionLoadResult(
        "kanban",
        PanelState.HEALTHY,
        {
            "_content_plugin": "kanban",
            "board_id": board_id,
            "event_sequence": sequence,
            "columns": [],
            "items": [],
        },
        "Kanban aktualisiert",
    )


class FakeLiveSync:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def run(self) -> None:
        self.started.set()
        await asyncio.Event().wait()

    def cancel(self) -> None:
        self.cancelled = True


def test_autoloader_reports_only_applied_snapshot_with_generation() -> None:
    async def scenario() -> None:
        registry = SectionAdapterRegistry()
        registry.register_async("kanban", lambda _section: _immediate(_snapshot(4)))
        tasks: list[asyncio.Task[None]] = []
        applied: list[SectionLoadResult] = []
        completed: list[tuple[SectionLoadResult, int]] = []
        loader = DashboardSectionAutoloader(
            registry,
            schedule=lambda awaitable: tasks.append(asyncio.create_task(awaitable))
            or tasks[-1],
            current_section=lambda: "kanban",
            apply_result=applied.append,
            on_snapshot_applied=lambda result, generation: completed.append(
                (result, generation)
            ),
        )

        assert loader.request("kanban") is True
        await asyncio.gather(*tasks)

        assert applied == [_snapshot(4)]
        assert completed == [(_snapshot(4), 1)]

    asyncio.run(scenario())


async def _immediate(result: SectionLoadResult) -> SectionLoadResult:
    await asyncio.sleep(0)
    return result


def test_lifecycle_starts_from_confirmed_watermark_and_stops_on_section_change() -> None:
    async def scenario() -> None:
        registry = SectionAdapterRegistry()
        section = ["kanban"]
        scheduled: list[asyncio.Task[None]] = []
        created: list[tuple[str, int, FakeLiveSync]] = []

        def factory(board_id, initial_sequence, _reload, _status):
            sync = FakeLiveSync()
            created.append((board_id, initial_sequence, sync))
            return sync

        lifecycle = DashboardLiveSyncLifecycle(
            endpoint="http://hub.test",
            credential="header.payload.signature",
            registry=registry,
            schedule=lambda awaitable: scheduled.append(
                asyncio.create_task(awaitable)
            )
            or scheduled[-1],
            current_section=lambda: section[0],
            apply_result=lambda _result: None,
            live_sync_factory=factory,
        )

        lifecycle.snapshot_applied(_snapshot(7), 3)
        await asyncio.wait_for(_wait_created(created), timeout=1)

        assert created[0][:2] == ("hub", 7)
        section[0] = "models"
        lifecycle.stop()
        await asyncio.sleep(0)
        assert created[0][2].cancelled is True

    asyncio.run(scenario())


async def _wait_created(created) -> None:
    while not created:
        await asyncio.sleep(0)


def test_live_reload_applies_only_matching_generation_and_board() -> None:
    async def scenario() -> None:
        registry = SectionAdapterRegistry()
        registry.register_async("kanban", lambda _section: _immediate(_snapshot(9)))
        section = ["kanban"]
        applied: list[SectionLoadResult] = []
        callbacks = {}

        class CompletingSync:
            cancelled = False

            async def run(self) -> None:
                callbacks["watermark"] = await callbacks["reload"]()

            def cancel(self) -> None:
                self.cancelled = True

        def factory(_board_id, _initial, reload_snapshot, _status):
            callbacks["reload"] = reload_snapshot
            return CompletingSync()

        lifecycle = DashboardLiveSyncLifecycle(
            endpoint="http://hub.test",
            credential="header.payload.signature",
            registry=registry,
            schedule=asyncio.create_task,
            current_section=lambda: section[0],
            apply_result=applied.append,
            live_sync_factory=factory,
        )

        lifecycle.snapshot_applied(_snapshot(7), 1)
        while "watermark" not in callbacks:
            await asyncio.sleep(0)

        assert callbacks["watermark"] == 9
        assert applied == [_snapshot(9)]
        lifecycle.stop()

    asyncio.run(scenario())


def test_interactive_run_always_cleans_autoload_and_live_sync() -> None:
    class App:
        def run(self, *, pre_run) -> None:
            assert callable(pre_run)

    class Cleanup:
        def __init__(self) -> None:
            self.called = 0

        def cancel(self) -> None:
            self.called += 1

        def stop(self) -> None:
            self.called += 1

    tui = InteractiveOperatorTui.__new__(InteractiveOperatorTui)
    autoload = Cleanup()
    live = Cleanup()
    tui._app = App()
    tui._on_app_start = lambda: None
    tui._dashboard_autoloader = autoload
    tui._dashboard_live_sync = live

    assert tui.run() == 0
    assert autoload.called == 1
    assert live.called == 1
