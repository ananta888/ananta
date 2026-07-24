from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest

from client_surfaces.operator_tui import sections
from client_surfaces.operator_tui.app import (
    _parse_args,
    build_initial_state,
    load_active_section,
)
from client_surfaces.operator_tui.dashboard_autoload import DashboardSectionAutoloader
from client_surfaces.operator_tui.interactive import InteractiveOperatorTui
from client_surfaces.operator_tui.models import (
    OperatorState,
    PanelState,
    SectionLoadResult,
)
from client_surfaces.operator_tui.adapters import SectionAdapterRegistry


class RecordingApplication:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task[None]] = []
        self.invalidations = 0

    def create_background_task(
        self,
        awaitable: Awaitable[None],
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(awaitable)
        self.tasks.append(task)
        return task

    def invalidate(self) -> None:
        self.invalidations += 1


class DeferredLoader:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, section_id: str) -> SectionLoadResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return _healthy(section_id, self.marker)


class CancellationResistantLoader(DeferredLoader):
    def __init__(self, marker: str) -> None:
        super().__init__(marker)
        self.cancelled = 0

    async def __call__(self, section_id: str) -> SectionLoadResult:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
        return _healthy(section_id, self.marker)


def _healthy(section_id: str, marker: str) -> SectionLoadResult:
    return SectionLoadResult(
        section_id,
        PanelState.HEALTHY,
        {
            "_content_plugin": section_id,
            "items": [{"id": marker}],
            "marker": marker,
        },
        f"{section_id} ready",
    )


def _build_tui(
    state: OperatorState,
    registry: SectionAdapterRegistry,
) -> tuple[InteractiveOperatorTui, RecordingApplication]:
    tui = InteractiveOperatorTui.__new__(InteractiveOperatorTui)
    app = RecordingApplication()
    tui._registry = registry
    tui._dashboard_controller = None
    tui._splash = None
    tui.state = load_active_section(state, registry)
    tui._rendered_text = ""
    tui._render = lambda: "rendered"  # type: ignore[method-assign]
    tui._header_3d_active = lambda: False  # type: ignore[method-assign]
    tui._app = app
    tui._dashboard_autoloader = DashboardSectionAutoloader(
        registry,
        schedule=lambda awaitable: tui._app.create_background_task(awaitable),
        current_section=lambda: tui.state.section_id,
        apply_result=tui._apply_dashboard_result,
    )
    return tui, app


async def _drain(app: RecordingApplication) -> None:
    if app.tasks:
        await asyncio.gather(*tuple(app.tasks), return_exceptions=True)


@pytest.mark.parametrize("section_id", ["kanban", "models"])
def test_startup_section_argument_autoloads_without_enter_or_click(
    monkeypatch,
    section_id: str,
) -> None:
    enabled = sections.dashboard_sections(
        {
            "ANANTA_TUI_KANBAN_ENABLED": "true",
            "ANANTA_TUI_MODEL_MENU_ENABLED": "true",
        }
    )
    core = tuple(item for item in sections.SECTIONS if item.id not in {"kanban", "models"})
    monkeypatch.setattr(sections, "SECTIONS", core + enabled)

    async def scenario() -> None:
        loader = DeferredLoader("startup")
        registry = SectionAdapterRegistry()
        registry.register_async(section_id, loader)
        state = build_initial_state(
            _parse_args(["--section", section_id, "--skip-splash"])
        ).with_updates(
            header_logo_game={
                "center_browser_active": True,
                "shortcut_help_middle_open": True,
            }
        )
        tui, app = _build_tui(state, registry)

        tui._on_app_start()
        await loader.started.wait()

        assert loader.calls == 1
        loader.release.set()
        await _drain(app)
        assert tui.state.panel_states[section_id] is PanelState.HEALTHY

    asyncio.run(scenario())


def test_navigation_to_dashboard_section_starts_one_async_load() -> None:
    async def scenario() -> None:
        loader = DeferredLoader("navigation")
        registry = SectionAdapterRegistry()
        registry.register_async("kanban", loader)
        tui, app = _build_tui(OperatorState(endpoint="", section_id="dashboard"), registry)

        next_state = load_active_section(
            tui.state.with_updates(section_id="kanban"),
            registry,
        )
        tui._set_state(next_state)
        await loader.started.wait()

        assert loader.calls == 1
        assert tui.state.panel_states["kanban"] is PanelState.LOADING
        loader.release.set()
        await _drain(app)
        assert tui.state.section_payloads["kanban"]["marker"] == "navigation"

    asyncio.run(scenario())


def test_inflight_autoload_is_deduplicated_and_does_not_block_event_loop() -> None:
    async def scenario() -> None:
        loader = DeferredLoader("deduped")
        registry = SectionAdapterRegistry()
        registry.register_async("models", loader)
        tui, app = _build_tui(OperatorState(endpoint="", section_id="models"), registry)

        assert tui._dashboard_autoloader.request("models") is True
        assert tui._dashboard_autoloader.request("models") is False
        await loader.started.wait()
        tick = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(tick.set_result, True)

        assert await asyncio.wait_for(tick, timeout=0.1) is True
        assert loader.calls == 1
        loader.release.set()
        await _drain(app)

    asyncio.run(scenario())


def test_section_change_cancels_and_ignores_stale_result() -> None:
    async def scenario() -> None:
        stale = CancellationResistantLoader("stale-kanban")
        registry = SectionAdapterRegistry()
        registry.register_async("kanban", stale)
        registry.register_async(
            "models",
            lambda section_id: _immediate(section_id, "current-models"),
        )
        tui, app = _build_tui(OperatorState(endpoint="", section_id="dashboard"), registry)

        tui._set_state(
            load_active_section(
                tui.state.with_updates(section_id="kanban"),
                registry,
            )
        )
        await stale.started.wait()
        tui._set_state(
            load_active_section(
                tui.state.with_updates(section_id="models"),
                registry,
            )
        )
        await _drain(app)

        assert stale.cancelled == 1
        assert tui.state.section_id == "models"
        assert tui.state.status_message == "models ready"
        assert tui.state.section_payloads["models"]["marker"] == "current-models"
        assert "marker" not in tui.state.section_payloads["kanban"]

    asyncio.run(scenario())


async def _immediate(section_id: str, marker: str) -> SectionLoadResult:
    await asyncio.sleep(0)
    return _healthy(section_id, marker)


def test_autoload_preserves_global_overlay_state() -> None:
    async def scenario() -> None:
        registry = SectionAdapterRegistry()
        registry.register_async(
            "kanban",
            lambda section_id: _immediate(section_id, "overlay"),
        )
        overlays = {
            "center_browser_active": True,
            "shortcut_help_middle_open": True,
            "visual_view_switcher_open": True,
        }
        tui, app = _build_tui(
            OperatorState(
                endpoint="",
                section_id="kanban",
                header_logo_game=overlays,
            ),
            registry,
        )

        tui._on_app_start()
        await _drain(app)

        assert tui.state.header_logo_game == overlays

    asyncio.run(scenario())


def test_permission_error_and_disabled_section_remain_fail_closed() -> None:
    async def denied(_section_id: str) -> SectionLoadResult:
        raise PermissionError("denied")

    async def scenario() -> None:
        registry = SectionAdapterRegistry()
        registry.register_async("kanban", denied)
        tui, app = _build_tui(OperatorState(endpoint="", section_id="kanban"), registry)

        tui._on_app_start()
        await _drain(app)

        assert tui.state.panel_states["kanban"] is PanelState.UNAUTHORIZED
        assert tui.state.section_payloads["kanban"] == {}

        disabled_tui, disabled_app = _build_tui(
            OperatorState(endpoint="", section_id="models"),
            SectionAdapterRegistry(),
        )
        disabled_tui._on_app_start()
        await asyncio.sleep(0)
        assert disabled_app.tasks == []

    asyncio.run(scenario())
