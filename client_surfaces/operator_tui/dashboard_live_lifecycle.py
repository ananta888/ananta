from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.dashboard_auth import (
    ResolvingDashboardTokenProvider,
)
from client_surfaces.operator_tui.dashboard_event_transport import (
    BoundedKanbanEventTransport,
)
from client_surfaces.operator_tui.dashboard_live_sync import KanbanLiveSync
from client_surfaces.operator_tui.models import PanelState, SectionLoadResult


class BackgroundTask(Protocol):
    def cancel(self) -> Any:
        ...

    def done(self) -> bool:
        ...


class LiveSyncRunner(Protocol):
    async def run(self) -> None:
        ...

    def cancel(self) -> None:
        ...


Schedule = Callable[[Awaitable[None]], BackgroundTask]
ApplyResult = Callable[[SectionLoadResult], None]
StatusSink = Callable[[str], None]
ReloadSnapshot = Callable[[], Awaitable[int | None]]
LiveSyncFactory = Callable[
    [str, int, ReloadSnapshot, StatusSink],
    LiveSyncRunner,
]


class DashboardLiveSyncLifecycle:
    """Own live sync for exactly one currently displayed Kanban snapshot."""

    def __init__(
        self,
        *,
        endpoint: str,
        credential: str,
        registry: SectionAdapterRegistry,
        schedule: Schedule,
        current_section: Callable[[], str],
        apply_result: ApplyResult,
        status_sink: StatusSink | None = None,
        live_sync_factory: LiveSyncFactory | None = None,
    ) -> None:
        self._registry = registry
        self._schedule = schedule
        self._current_section = current_section
        self._apply_result = apply_result
        self._status_sink = status_sink or (lambda _status: None)
        token_provider = ResolvingDashboardTokenProvider(
            endpoint=endpoint,
            credential=credential,
        )

        def default_factory(
            board_id: str,
            initial_sequence: int,
            reload_snapshot: ReloadSnapshot,
            status_sink: StatusSink,
        ) -> LiveSyncRunner:
            return KanbanLiveSync(
                board_id=board_id,
                initial_sequence=initial_sequence,
                token_provider=token_provider,
                transport=BoundedKanbanEventTransport(endpoint=endpoint),
                reload_snapshot=reload_snapshot,
                status_sink=status_sink,
            )

        self._factory = live_sync_factory or default_factory
        self._generation = 0
        self._autoload_generation = 0
        self._board_id = ""
        self._sync: LiveSyncRunner | None = None
        self._runner: BackgroundTask | None = None

    def snapshot_applied(
        self,
        result: SectionLoadResult,
        autoload_generation: int,
    ) -> None:
        identity = self._snapshot_identity(result)
        if identity is None or self._current_section() != "kanban":
            self.stop()
            return
        board_id, event_sequence = identity
        if (
            self._runner is not None
            and not self._runner.done()
            and self._autoload_generation == autoload_generation
            and self._board_id == board_id
        ):
            return
        self.stop()
        self._autoload_generation = int(autoload_generation)
        self._board_id = board_id
        generation = self._generation
        self._runner = self._schedule(
            self._run_generation(
                generation=generation,
                board_id=board_id,
                initial_sequence=event_sequence,
            )
        )

    def stop(self) -> None:
        self._generation += 1
        sync = self._sync
        runner = self._runner
        self._sync = None
        self._runner = None
        self._board_id = ""
        if sync is not None:
            sync.cancel()
        if runner is not None and not runner.done():
            runner.cancel()

    async def _run_generation(
        self,
        *,
        generation: int,
        board_id: str,
        initial_sequence: int,
    ) -> None:
        if not self._guard(generation, board_id):
            return

        async def reload_snapshot() -> int | None:
            if not self._guard(generation, board_id):
                return None
            result = await self._registry.load_async("kanban")
            if not self._guard(generation, board_id):
                return None
            identity = self._snapshot_identity(result)
            if identity is None or identity[0] != board_id:
                self._status("kanban_live_sync_snapshot_identity_mismatch")
                return None
            self._apply_result(result)
            return identity[1]

        def guarded_status(status: str) -> None:
            if self._guard(generation, board_id):
                self._status(status)

        sync = self._factory(
            board_id,
            initial_sequence,
            reload_snapshot,
            guarded_status,
        )
        if not self._guard(generation, board_id):
            sync.cancel()
            return
        self._sync = sync
        try:
            await sync.run()
        except asyncio.CancelledError:
            sync.cancel()
            raise
        finally:
            if self._generation == generation and self._sync is sync:
                self._sync = None

    def _guard(self, generation: int, board_id: str) -> bool:
        return (
            generation == self._generation
            and board_id == self._board_id
            and self._current_section() == "kanban"
        )

    def _status(self, status: str) -> None:
        self._status_sink(str(status or "kanban_live_sync_status"))

    @staticmethod
    def _snapshot_identity(
        result: SectionLoadResult,
    ) -> tuple[str, int] | None:
        if result.section_id != "kanban" or result.state is not PanelState.HEALTHY:
            return None
        payload = result.payload if isinstance(result.payload, dict) else {}
        board_id = str(payload.get("board_id") or "").strip()
        sequence = payload.get("event_sequence")
        if (
            not board_id
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 0
        ):
            return None
        return board_id, sequence
