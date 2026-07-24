from __future__ import annotations

import asyncio
from collections import deque

from ananta_contracts.kanban_events import KanbanEvent
from client_surfaces.operator_tui.dashboard_auth import (
    DashboardReauthenticationRequired,
)
from client_surfaces.operator_tui.dashboard_event_transport import (
    KanbanEventBatch,
    KanbanEventTransportError,
)
from client_surfaces.operator_tui.dashboard_live_sync import (
    KanbanEventSequencePolicy,
    KanbanLiveSync,
)


def _event(sequence: int, *, board_id: str = "hub") -> KanbanEvent:
    return KanbanEvent.model_validate(
        {
            "schema_version": "kanban.event.v1",
            "event_id": str(sequence),
            "board_id": board_id,
            "task_id": "TASK-1",
            "revision": sequence,
            "sequence": sequence,
            "event_type": "kanban.card.moved",
            "occurred_at": "2026-07-23T12:00:00Z",
            "payload": {"column_id": "in_progress", "position": 0},
        }
    )


def _batch(
    *events: KanbanEvent,
    gap: bool = False,
    snapshot_required: bool = False,
    latest: int | None = None,
    has_more: bool = False,
) -> KanbanEventBatch:
    last = events[-1].sequence if events else 0
    return KanbanEventBatch(
        events=tuple(events),
        gap_detected=gap,
        gap_reason="bounded_history_overflow" if gap else "",
        overflow_reason="bounded_history_overflow" if gap else "",
        snapshot_required=snapshot_required,
        snapshot_url="/api/v1/kanban/boards/hub" if snapshot_required else "",
        next_after_sequence=last,
        latest_sequence=latest if latest is not None else last,
        has_more=has_more,
    )


class FakeTokenProvider:
    def __init__(self, *, static: bool = False) -> None:
        self.static = static
        self.calls: list[bool] = []

    def access_token(self, *, force_refresh: bool = False) -> str:
        self.calls.append(force_refresh)
        if force_refresh and self.static:
            raise DashboardReauthenticationRequired()
        return "fresh" if force_refresh else "initial"


class ScriptedTransport:
    def __init__(self, *items) -> None:
        self.items = deque(items)
        self.calls: list[tuple[int, str]] = []
        self.closed = False
        self.exhausted = asyncio.Event()

    async def fetch(self, *, board_id: str, after_sequence: int, token: str):
        assert board_id == "hub"
        self.calls.append((after_sequence, token))
        if not self.items:
            self.exhausted.set()
            await asyncio.Event().wait()
        item = self.items.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


def test_sequence_policy_deduplicates_and_detects_gap() -> None:
    policy = KanbanEventSequencePolicy()

    ordered = policy.evaluate(
        board_id="hub",
        current_sequence=1,
        batch=_batch(_event(1), _event(2)),
    )
    gap = policy.evaluate(
        board_id="hub",
        current_sequence=2,
        batch=_batch(_event(4), latest=4),
    )

    assert ordered.candidate_sequence == 2
    assert ordered.duplicate_count == 1
    assert ordered.requires_snapshot_watermark is False
    assert gap.reason == "client_sequence_gap"
    assert gap.requires_snapshot_watermark is True


def test_ordered_batch_coalesces_to_one_snapshot_and_advances() -> None:
    async def scenario() -> None:
        transport = ScriptedTransport(_batch(_event(1), _event(2)))
        reloaded = asyncio.Event()
        reloads = 0

        async def reload_snapshot() -> int | None:
            nonlocal reloads
            reloads += 1
            reloaded.set()
            return None

        sync = KanbanLiveSync(
            board_id="hub",
            token_provider=FakeTokenProvider(),
            transport=transport,
            reload_snapshot=reload_snapshot,
            poll_interval_seconds=60,
        )
        sync.start()
        await asyncio.wait_for(reloaded.wait(), timeout=1)

        assert reloads == 1
        assert sync.sequence == 2
        await sync.stop()
        assert transport.closed is True

    asyncio.run(scenario())


def test_gap_requires_authoritative_snapshot_watermark() -> None:
    async def scenario() -> None:
        statuses: list[str] = []
        transport = ScriptedTransport(
            _batch(gap=True, snapshot_required=True, latest=7)
        )

        async def reload_snapshot() -> int | None:
            return None

        sync = KanbanLiveSync(
            board_id="hub",
            token_provider=FakeTokenProvider(),
            transport=transport,
            reload_snapshot=reload_snapshot,
            status_sink=statuses.append,
        )
        task = sync.start()
        await asyncio.wait_for(task, timeout=1)

        assert sync.sequence == 0
        assert statuses[-1] == "kanban_live_sync_snapshot_watermark_required"
        await sync.stop()

    asyncio.run(scenario())


def test_gap_advances_only_to_returned_snapshot_watermark() -> None:
    async def scenario() -> None:
        loaded = asyncio.Event()
        transport = ScriptedTransport(
            _batch(gap=True, snapshot_required=True, latest=7)
        )

        async def reload_snapshot() -> int | None:
            loaded.set()
            return 9

        sync = KanbanLiveSync(
            board_id="hub",
            token_provider=FakeTokenProvider(),
            transport=transport,
            reload_snapshot=reload_snapshot,
            poll_interval_seconds=60,
        )
        sync.start()
        await asyncio.wait_for(loaded.wait(), timeout=1)

        assert sync.sequence == 9
        await sync.stop()

    asyncio.run(scenario())


def test_401_refreshes_once_and_403_stops_fail_closed() -> None:
    async def scenario() -> None:
        provider = FakeTokenProvider()
        statuses: list[str] = []
        transport = ScriptedTransport(
            KanbanEventTransportError(
                "token_expired",
                status_code=401,
            ),
            KanbanEventTransportError(
                "kanban_forbidden",
                status_code=403,
                retryable=False,
            ),
        )

        async def reload_snapshot() -> int | None:
            raise AssertionError("no snapshot expected")

        sync = KanbanLiveSync(
            board_id="hub",
            token_provider=provider,
            transport=transport,
            reload_snapshot=reload_snapshot,
            status_sink=statuses.append,
        )
        await asyncio.wait_for(sync.run(), timeout=1)

        assert provider.calls == [False, True]
        assert transport.calls == [(0, "initial"), (0, "fresh")]
        assert statuses == [
            "kanban_live_sync_auth_refresh",
            "kanban_live_sync_forbidden",
        ]

    asyncio.run(scenario())


def test_static_jwt_401_requires_reauthentication_without_second_request() -> None:
    async def scenario() -> None:
        provider = FakeTokenProvider(static=True)
        statuses: list[str] = []
        transport = ScriptedTransport(
            KanbanEventTransportError("token_expired", status_code=401)
        )

        async def reload_snapshot() -> int | None:
            return None

        sync = KanbanLiveSync(
            board_id="hub",
            token_provider=provider,
            transport=transport,
            reload_snapshot=reload_snapshot,
            status_sink=statuses.append,
        )
        await asyncio.wait_for(sync.run(), timeout=1)

        assert provider.calls == [False, True]
        assert transport.calls == [(0, "initial")]
        assert statuses[-1] == "dashboard_reauthentication_required"

    asyncio.run(scenario())


def test_retryable_failure_uses_backoff_without_advancing_cursor() -> None:
    async def scenario() -> None:
        sleeps: list[float] = []
        loaded = asyncio.Event()
        transport = ScriptedTransport(
            KanbanEventTransportError(
                "temporary",
                status_code=503,
            ),
            _batch(_event(1)),
        )

        async def sleeper(value: float) -> None:
            sleeps.append(value)
            await asyncio.sleep(0)

        async def reload_snapshot() -> int | None:
            loaded.set()
            return None

        sync = KanbanLiveSync(
            board_id="hub",
            token_provider=FakeTokenProvider(),
            transport=transport,
            reload_snapshot=reload_snapshot,
            poll_interval_seconds=60,
            backoff_seconds=(0.1, 0.2),
            sleeper=sleeper,
        )
        sync.start()
        await asyncio.wait_for(loaded.wait(), timeout=1)

        assert transport.calls[:2] == [(0, "initial"), (0, "initial")]
        assert sleeps == [0.1, 60.0]
        assert sync.sequence == 1
        await sync.stop()

    asyncio.run(scenario())
