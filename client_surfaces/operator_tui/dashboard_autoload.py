from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.models import SectionLoadResult


class BackgroundTask(Protocol):
    def cancel(self) -> bool: ...

    def done(self) -> bool: ...


TaskScheduler = Callable[[Coroutine[Any, Any, None]], BackgroundTask]


class DashboardSectionAutoloader:
    """Coordinate one non-blocking dashboard load for the active section."""

    def __init__(
        self,
        registry: SectionAdapterRegistry,
        *,
        schedule: TaskScheduler,
        current_section: Callable[[], str],
        apply_result: Callable[[SectionLoadResult], None],
        on_snapshot_applied: Callable[[SectionLoadResult, int], None] | None = None,
    ) -> None:
        self._registry = registry
        self._schedule = schedule
        self._current_section = current_section
        self._apply_result = apply_result
        self._on_snapshot_applied = on_snapshot_applied
        self._task: BackgroundTask | None = None
        self._task_section = ""
        self._generation = 0

    def is_loading(self, section_id: str) -> bool:
        candidate = str(section_id or "").strip().lower()
        return (
            self._task is not None
            and not self._task.done()
            and self._task_section == candidate
        )

    def request(self, section_id: str) -> bool:
        candidate = str(section_id or "").strip().lower()
        if candidate not in self._registry.registered_async_sections():
            self.cancel()
            return False
        if self.is_loading(candidate):
            return False

        self._generation += 1
        generation = self._generation
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task_section = candidate
        self._task = self._schedule(self._load(candidate, generation))
        return True

    def cancel(self) -> None:
        self._generation += 1
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._task_section = ""

    async def _load(self, section_id: str, generation: int) -> None:
        try:
            result = await self._registry.load_async(section_id)
        except asyncio.CancelledError:
            return
        if generation != self._generation:
            return
        if self._current_section() != section_id:
            return
        self._apply_result(result)
        if self._on_snapshot_applied is not None:
            self._on_snapshot_applied(result, generation)
