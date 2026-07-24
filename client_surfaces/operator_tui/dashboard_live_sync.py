from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.dashboard_auth import (
    DashboardReauthenticationRequired,
    DashboardTokenProvider,
)
from client_surfaces.operator_tui.dashboard_event_transport import (
    KanbanEventBatch,
    KanbanEventContractError,
    KanbanEventTransport,
    KanbanEventTransportError,
)

SnapshotReload = Callable[[], Awaitable[int | None]]
StatusSink = Callable[[str], None]
Sleeper = Callable[[float], Awaitable[Any]]


@dataclass(frozen=True)
class KanbanSyncDecision:
    reload_snapshot: bool
    candidate_sequence: int
    requires_snapshot_watermark: bool
    reason: str
    duplicate_count: int = 0


class KanbanEventSequencePolicy:
    """Pure sequence policy; it never performs transport or UI work."""

    def evaluate(
        self,
        *,
        board_id: str,
        current_sequence: int,
        batch: KanbanEventBatch,
    ) -> KanbanSyncDecision:
        if batch.gap_detected or batch.snapshot_required:
            return KanbanSyncDecision(
                reload_snapshot=True,
                candidate_sequence=max(current_sequence, batch.latest_sequence),
                requires_snapshot_watermark=True,
                reason=(
                    batch.gap_reason
                    or batch.overflow_reason
                    or "snapshot_required"
                ),
            )

        cursor = current_sequence
        duplicates = 0
        changed = False
        for event in batch.events:
            if event.board_id != board_id:
                raise KanbanEventContractError("kanban_event_board_mismatch")
            sequence = event.sequence
            if sequence <= cursor:
                duplicates += 1
                continue
            if sequence != cursor + 1:
                return KanbanSyncDecision(
                    reload_snapshot=True,
                    candidate_sequence=max(batch.latest_sequence, sequence),
                    requires_snapshot_watermark=True,
                    reason="client_sequence_gap",
                    duplicate_count=duplicates,
                )
            cursor = sequence
            changed = True

        if batch.next_after_sequence < cursor:
            raise KanbanEventContractError("kanban_event_cursor_regression")
        return KanbanSyncDecision(
            reload_snapshot=changed,
            candidate_sequence=cursor,
            requires_snapshot_watermark=False,
            reason="events_applied" if changed else "no_change",
            duplicate_count=duplicates,
        )


class KanbanLiveSync:
    """Single-owner polling/reconnect lifecycle for a Kanban section."""

    def __init__(
        self,
        *,
        board_id: str,
        token_provider: DashboardTokenProvider,
        transport: KanbanEventTransport,
        reload_snapshot: SnapshotReload,
        sequence_policy: KanbanEventSequencePolicy | None = None,
        status_sink: StatusSink | None = None,
        initial_sequence: int = 0,
        poll_interval_seconds: float = 0.5,
        backoff_seconds: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 5.0),
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        normalized_board = str(board_id or "").strip()
        if not normalized_board:
            raise ValueError("kanban_live_sync_board_required")
        if not backoff_seconds or any(value < 0 for value in backoff_seconds):
            raise ValueError("kanban_live_sync_backoff_invalid")
        if isinstance(initial_sequence, bool) or int(initial_sequence) < 0:
            raise ValueError("kanban_live_sync_initial_sequence_invalid")
        self._board_id = normalized_board
        self._token_provider = token_provider
        self._transport = transport
        self._reload_snapshot = reload_snapshot
        self._policy = sequence_policy or KanbanEventSequencePolicy()
        self._status_sink = status_sink or (lambda _status: None)
        self._poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self._backoff_seconds = tuple(float(value) for value in backoff_seconds)
        self._sleep = sleeper
        self._sequence = int(initial_sequence)
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def sequence(self) -> int:
        return self._sequence

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> asyncio.Task[None]:
        if self._closed:
            raise RuntimeError("kanban_live_sync_closed")
        if self.running:
            return self._task  # type: ignore[return-value]
        self._task = asyncio.create_task(self.run(), name="tui-kanban-live-sync")
        return self._task

    def cancel(self) -> None:
        self._closed = True
        self._transport.close()
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def stop(self) -> None:
        self.cancel()
        task = self._task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None

    async def run(self) -> None:
        force_refresh = False
        backoff_index = 0
        while not self._closed:
            try:
                token = await asyncio.to_thread(
                    self._token_provider.access_token,
                    force_refresh=force_refresh,
                )
            except DashboardReauthenticationRequired:
                self._status_sink("dashboard_reauthentication_required")
                return
            except Exception:
                self._status_sink("dashboard_authentication_failed")
                return

            try:
                batch = await self._transport.fetch(
                    board_id=self._board_id,
                    after_sequence=self._sequence,
                    token=token,
                )
            except asyncio.CancelledError:
                raise
            except KanbanEventTransportError as exc:
                if exc.status_code == 401 and not force_refresh:
                    force_refresh = True
                    self._status_sink("kanban_live_sync_auth_refresh")
                    continue
                if exc.status_code == 401:
                    self._status_sink("dashboard_reauthentication_required")
                    return
                if exc.status_code == 403:
                    self._status_sink("kanban_live_sync_forbidden")
                    return
                if not exc.retryable:
                    self._status_sink(exc.code)
                    return
                self._status_sink("kanban_live_sync_reconnecting")
                await self._sleep(self._backoff_seconds[backoff_index])
                backoff_index = min(
                    backoff_index + 1,
                    len(self._backoff_seconds) - 1,
                )
                continue

            force_refresh = False
            backoff_index = 0
            try:
                decision = self._policy.evaluate(
                    board_id=self._board_id,
                    current_sequence=self._sequence,
                    batch=batch,
                )
            except KanbanEventContractError as exc:
                self._status_sink(exc.code)
                return

            if decision.reload_snapshot:
                try:
                    watermark = await self._reload_snapshot()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._status_sink("kanban_live_sync_snapshot_failed")
                    await self._sleep(self._backoff_seconds[backoff_index])
                    backoff_index = min(
                        backoff_index + 1,
                        len(self._backoff_seconds) - 1,
                    )
                    continue
                if decision.requires_snapshot_watermark:
                    if (
                        isinstance(watermark, bool)
                        or not isinstance(watermark, int)
                        or watermark < decision.candidate_sequence
                    ):
                        self._status_sink(
                            "kanban_live_sync_snapshot_watermark_required"
                        )
                        return
                    self._sequence = watermark
                else:
                    self._sequence = (
                        watermark
                        if isinstance(watermark, int)
                        and not isinstance(watermark, bool)
                        and watermark >= decision.candidate_sequence
                        else decision.candidate_sequence
                    )
                self._status_sink("kanban_live_sync_snapshot_loaded")

            if batch.has_more:
                continue
            await self._sleep(self._poll_interval_seconds)
