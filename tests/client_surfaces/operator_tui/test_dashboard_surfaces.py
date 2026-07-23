from __future__ import annotations

import asyncio

import pytest

from client_surfaces.operator_tui import _renderer_content
from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.dashboard_surfaces import (
    DashboardFeatureFlags,
    DashboardSurfaceController,
    KanbanContentPlugin,
    ModelCatalogContentPlugin,
    RevisionConflict,
    dashboard_region_rects,
    normalise_catalog,
)
from client_surfaces.operator_tui.models import FocusPane, OperatorState, PanelState, Section
from client_surfaces.operator_tui.mouse import MouseState
from client_surfaces.operator_tui.region_index import RegionIndex
from client_surfaces.operator_tui.sections import dashboard_sections


class FakeKanbanPort:
    def __init__(self) -> None:
        self.revision = 7
        self.calls: list[tuple[str, dict]] = []
        self.conflict_once = False

    def snapshot(self) -> dict:
        return {
            "revision": self.revision,
            "columns": [
                {
                    "id": "todo",
                    "title": "Todo",
                    "tasks": [
                        {
                            "id": "TASK-1",
                            "title": "First",
                            "status": "todo",
                            "priority": "P0",
                            "revision": self.revision,
                        },
                        {
                            "id": "TASK-2",
                            "title": "Second",
                            "status": "todo",
                            "priority": "P1",
                            "revision": self.revision,
                        },
                    ],
                },
                {
                    "id": "done",
                    "title": "Done",
                    "tasks": [
                        {
                            "id": "TASK-3",
                            "title": "Third",
                            "status": "done",
                            "priority": "P1",
                            "revision": self.revision,
                        }
                    ],
                },
            ],
        }

    async def fetch_board(self) -> dict:
        self.calls.append(("fetch", {}))
        return self.snapshot()

    async def _change(self, name: str, data: dict) -> dict:
        self.calls.append((name, data))
        if self.conflict_once:
            self.conflict_once = False
            self.revision += 1
            raise RevisionConflict(current_revision=self.revision)
        self.revision += 1
        return {"revision": self.revision}

    async def move_task(self, task_id: str, **kwargs) -> dict:
        return await self._change("move", {"task_id": task_id, **kwargs})

    async def assign_task(self, task_id: str, **kwargs) -> dict:
        return await self._change("assign", {"task_id": task_id, **kwargs})

    async def comment_task(self, task_id: str, **kwargs) -> dict:
        return await self._change("comment", {"task_id": task_id, **kwargs})

    async def block_task(self, task_id: str, **kwargs) -> dict:
        return await self._change("block", {"task_id": task_id, **kwargs})

    async def complete_task(self, task_id: str, **kwargs) -> dict:
        return await self._change("complete", {"task_id": task_id, **kwargs})


class FakeModelCatalogPort:
    def __init__(self) -> None:
        self.revision = 3
        self.calls: list[tuple[str, dict]] = []

    async def fetch_catalog(self) -> dict:
        self.calls.append(("fetch", {}))
        return {
            "revision": self.revision,
            "models": [
                {
                    "id": "safe-model",
                    "provider": "local",
                    "healthy": True,
                    "available": True,
                    "loaded": True,
                    "default": True,
                    "url": "https://must-not-leak.invalid",
                    "token": "must-not-leak",
                },
                {
                    "id": "cloud-model",
                    "provider": "cloud",
                    "healthy": True,
                    "available": True,
                    "default": False,
                },
            ],
            "provider_errors": [{"provider": "broken", "code": "unavailable", "secret": "no"}],
        }

    async def refresh_catalog(self) -> dict:
        self.calls.append(("refresh", {}))
        self.revision += 1
        return {"revision": self.revision}

    async def set_default(self, model_id: str, **kwargs) -> dict:
        self.calls.append(("default", {"model_id": model_id, **kwargs}))
        self.revision += 1
        return {"revision": self.revision}


def _controller() -> tuple[DashboardSurfaceController, FakeKanbanPort, FakeModelCatalogPort]:
    kanban = FakeKanbanPort()
    models = FakeModelCatalogPort()
    controller = DashboardSurfaceController(
        kanban_port=kanban,
        model_catalog_port=models,
        flags=DashboardFeatureFlags(kanban=True, models=True),
    )
    return controller, kanban, models


def test_feature_flags_are_default_false_and_sections_are_fail_closed() -> None:
    assert DashboardFeatureFlags.from_mapping({}).enabled_section_ids() == ()
    assert dashboard_sections({}) == ()
    sections = dashboard_sections(
        {
            "ANANTA_TUI_KANBAN_ENABLED": "true",
            "ANANTA_TUI_MODEL_MENU_ENABLED": "1",
        }
    )
    assert tuple(section.id for section in sections) == ("kanban", "models")


def test_async_section_registry_uses_injected_ports_and_serializable_payloads() -> None:
    controller, _, _ = _controller()
    registry = SectionAdapterRegistry()
    controller.register(registry)

    assert registry.registered_async_sections() == ("kanban", "models")
    assert registry.load("kanban").state is PanelState.LOADING
    result = asyncio.run(registry.load_async("kanban"))
    assert result.state is PanelState.HEALTHY
    assert result.payload["items"][0]["id"] == "TASK-1"
    assert registry.load("kanban") == result


@pytest.mark.parametrize(
    ("width", "height", "layout"),
    [(80, 24, "layout:list"), (120, 30, "layout:columns"), (160, 40, "layout:columns")],
)
def test_kanban_render_and_regions_are_deterministic_for_terminal_sizes(
    width: int,
    height: int,
    layout: str,
) -> None:
    controller, _, _ = _controller()
    payload = asyncio.run(controller.load_kanban()).payload
    plugin = KanbanContentPlugin()

    first = plugin.render(payload, width, height, selected=1)
    second = plugin.render(payload, width, height, selected=1)
    assert first == second
    assert layout in first[0]
    assert all(len(line) <= width for line in first)

    regions = dashboard_region_rects(
        "kanban",
        payload,
        x1=0,
        x2=width - 1,
        y1=0,
        y2=height - 1,
    )
    assert len(regions) == 3
    assert all(0 <= region.x1 <= region.x2 < width for region in regions)
    assert all(0 <= region.y1 <= region.y2 < height for region in regions)


def test_mouse_and_keyboard_share_selection_state() -> None:
    controller, _, _ = _controller()
    payload = asyncio.run(controller.load_kanban()).payload
    regions = RegionIndex(
        dashboard_region_rects("kanban", payload, x1=0, x2=79, y1=0, y2=23)
    )

    mouse, target, result = asyncio.run(
        controller.handle_mouse(
            MouseState(),
            x=5,
            y=3,
            width=80,
            height=24,
            event_type="down",
            region_index=regions,
            now=10.0,
        )
    )

    assert mouse.active is True
    assert target is not None
    assert controller.selected_index("kanban") == 1
    assert result is not None and result.state is PanelState.HEALTHY
    assert controller.move_selection("kanban", 1) == 2


def test_kanban_commands_forward_revision_and_reload_on_conflict() -> None:
    controller, port, _ = _controller()
    asyncio.run(controller.load_kanban())
    controller.select("kanban", 1)

    asyncio.run(controller.move_selected("done", target_position=0))
    asyncio.run(controller.assign_selected("worker-7"))
    asyncio.run(controller.comment_selected("checked"))
    asyncio.run(controller.block_selected("dependency"))
    asyncio.run(controller.complete_selected())

    command_calls = [call for call in port.calls if call[0] != "fetch"]
    assert [name for name, _ in command_calls] == [
        "move",
        "assign",
        "comment",
        "block",
        "complete",
    ]
    assert all(data["task_id"] == "TASK-2" for _, data in command_calls)
    assert all("expected_revision" in data for _, data in command_calls)

    port.conflict_once = True
    conflict = asyncio.run(controller.move_selected("done"))
    assert conflict.state is PanelState.HEALTHY
    assert conflict.payload["revision_conflict"] is True
    assert "Snapshot neu geladen" in conflict.message


def test_model_catalog_strips_transport_data_and_supports_only_safe_actions() -> None:
    controller, _, port = _controller()
    loaded = asyncio.run(controller.load_models())
    model = loaded.payload["models"][0]

    assert "url" not in model
    assert "token" not in model
    assert loaded.payload["provider_errors"] == [
        {"provider": "broken", "code": "unavailable"}
    ]
    assert "load_model" not in dir(controller)
    assert "unload_model" not in dir(controller)

    controller.select("models", 1)
    refreshed = asyncio.run(controller.refresh_models())
    defaulted = asyncio.run(controller.set_selected_default())
    assert refreshed.state is PanelState.HEALTHY
    assert defaulted.state is PanelState.HEALTHY
    assert ("refresh", {}) in port.calls
    assert any(
        name == "default"
        and data["model_id"] == "cloud-model"
        and "expected_revision" in data
        for name, data in port.calls
    )


def test_model_plugin_is_deterministic_and_provider_errors_are_isolated() -> None:
    payload = normalise_catalog(
        asyncio.run(FakeModelCatalogPort().fetch_catalog())
    )
    plugin = ModelCatalogContentPlugin()

    lines = plugin.render(payload, 80, 24, selected=0)
    assert lines == plugin.render(payload, 80, 24, selected=0)
    assert any("safe-model" in line for line in lines)
    assert any("cloud-model" in line for line in lines)
    assert any("provider errors:1" in line for line in lines)


@pytest.mark.parametrize("section_id", ["kanban", "models"])
@pytest.mark.parametrize(
    ("overlay_key", "helper_name", "marker"),
    [
        ("shortcut_help_middle_open", "_content_shortcut_lines", "SHORTCUT_OVERLAY"),
        ("center_browser_active", "_content_browser_lines", "BROWSER_OVERLAY"),
    ],
)
def test_global_overlays_keep_priority_over_dashboard_plugins(
    monkeypatch,
    section_id: str,
    overlay_key: str,
    helper_name: str,
    marker: str,
) -> None:
    section = Section(
        section_id,
        section_id.title(),
        True,
        (),
        "empty_or_degraded_panel",
    )
    monkeypatch.setattr(_renderer_content, "get_section", lambda _section_id: section)
    if helper_name == "_content_shortcut_lines":
        monkeypatch.setattr(
            _renderer_content,
            helper_name,
            lambda state, width: [marker],
        )
    else:
        monkeypatch.setattr(
            _renderer_content,
            helper_name,
            lambda game, width, *, height=None: [marker],
        )
    payload = {
        "_content_plugin": section_id,
        "revision": 1,
        "columns": [{"id": "todo", "title": "Todo", "tasks": []}],
        "models": [{"id": "model", "provider": "local"}],
        "items": [{"id": "item"}],
    }
    state = OperatorState(
        endpoint="",
        section_id=section_id,
        focus=FocusPane.CONTENT,
        panel_states={section_id: PanelState.HEALTHY},
        section_payloads={section_id: payload},
        header_logo_game={overlay_key: True},
    )

    lines = _renderer_content._content_lines(state, 80, height=20)

    assert marker in lines
    assert not any("layout:" in line or "catalog revision:" in line for line in lines)
