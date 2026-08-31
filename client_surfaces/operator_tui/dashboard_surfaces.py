from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from client_surfaces.operator_tui.adapters import SectionAdapterRegistry
from client_surfaces.operator_tui.kanban_viewport import plan_kanban_viewport
from client_surfaces.operator_tui.models import PanelState, SectionLoadResult
from client_surfaces.operator_tui.mouse import MouseState, normalize_mouse_state
from client_surfaces.operator_tui.plugins import ContentPlugin
from client_surfaces.operator_tui.region_index import RegionIndex, RegionRect, RegionTarget

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _safe_text(value: object, *, limit: int = 500) -> str:
    text = _CONTROL_CHARS.sub("", str(value or "")).replace("\x1b", "")
    return text.strip()[:limit]


def _safe_sequence(value: object, *, limit: int = 50) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_safe_text(item, limit=120) for item in value[:limit]]


@dataclass(frozen=True)
class DashboardFeatureFlags:
    kanban: bool = False
    models: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, str] | None = None) -> DashboardFeatureFlags:
        source = os.environ if values is None else values
        return cls(
            kanban=str(source.get("ANANTA_TUI_KANBAN_ENABLED") or "").strip().lower()
            in _TRUE_VALUES,
            models=str(source.get("ANANTA_TUI_MODEL_MENU_ENABLED") or "").strip().lower()
            in _TRUE_VALUES,
        )

    def enabled_section_ids(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.kanban:
            result.append("kanban")
        if self.models:
            result.append("models")
        return tuple(result)


class RevisionConflict(RuntimeError):
    def __init__(self, message: str = "revision conflict", *, current_revision: object = None) -> None:
        super().__init__(message)
        self.status_code = 409
        self.current_revision = current_revision


class KanbanBackendPort(Protocol):
    async def fetch_board(self) -> Mapping[str, Any]: ...

    async def move_task(
        self,
        task_id: str,
        *,
        target_status: str,
        target_position: int | None,
        expected_revision: object,
    ) -> Mapping[str, Any]: ...

    async def assign_task(
        self,
        task_id: str,
        *,
        assignee_id: str,
        expected_revision: object,
    ) -> Mapping[str, Any]: ...

    async def comment_task(
        self,
        task_id: str,
        *,
        body: str,
        expected_revision: object,
    ) -> Mapping[str, Any]: ...

    async def block_task(
        self,
        task_id: str,
        *,
        reason: str,
        expected_revision: object,
    ) -> Mapping[str, Any]: ...

    async def complete_task(
        self,
        task_id: str,
        *,
        expected_revision: object,
    ) -> Mapping[str, Any]: ...


class ModelCatalogPort(Protocol):
    async def fetch_catalog(self) -> Mapping[str, Any]: ...

    async def refresh_catalog(self) -> Mapping[str, Any]: ...

    async def set_default(
        self,
        model_id: str,
        *,
        expected_revision: object,
    ) -> Mapping[str, Any]: ...


def _normalise_task(raw: Mapping[str, Any], board_revision: object, flat_index: int) -> dict[str, Any]:
    return {
        "kind": "kanban_card",
        "id": _safe_text(raw.get("id"), limit=160),
        "title": _safe_text(raw.get("title") or raw.get("name") or raw.get("id"), limit=240),
        "status": _safe_text(raw.get("status"), limit=80),
        "priority": _safe_text(raw.get("priority"), limit=40),
        "assignee": _safe_text(raw.get("assignee") or raw.get("assignee_id"), limit=160),
        "labels": _safe_sequence(raw.get("labels"), limit=20),
        "blocked": bool(raw.get("blocked")),
        "blocked_reason": _safe_text(raw.get("blocked_reason"), limit=300),
        "description": _safe_text(raw.get("description"), limit=500),
        "dependencies": _safe_sequence(raw.get("dependencies"), limit=50),
        "last_activity": _safe_text(raw.get("last_activity"), limit=160),
        "revision": raw.get("revision", board_revision),
        "allowed_transitions": _safe_sequence(raw.get("allowed_transitions"), limit=20),
        "assignable_assignees": _safe_sequence(raw.get("assignable_assignees"), limit=50),
        "_flat_index": flat_index,
    }


def normalise_board(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    board_revision = snapshot.get("revision")
    columns: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    raw_columns = snapshot.get("columns")
    if not isinstance(raw_columns, list):
        raw_columns = []
    for column_index, raw_column in enumerate(raw_columns):
        if not isinstance(raw_column, Mapping):
            continue
        tasks: list[dict[str, Any]] = []
        raw_tasks = raw_column.get("tasks")
        if not isinstance(raw_tasks, list):
            raw_tasks = raw_column.get("items")
        if not isinstance(raw_tasks, list):
            raw_tasks = []
        for raw_task in raw_tasks:
            if not isinstance(raw_task, Mapping):
                continue
            task = _normalise_task(raw_task, board_revision, len(items))
            if not task["id"]:
                continue
            tasks.append(task)
            items.append(task)
        columns.append(
            {
                "id": _safe_text(raw_column.get("id") or f"column-{column_index}", limit=120),
                "title": _safe_text(
                    raw_column.get("title") or raw_column.get("name") or raw_column.get("id"),
                    limit=120,
                ),
                "wip_limit": raw_column.get("wip_limit"),
                "tasks": tasks,
            }
        )
    return {
        "_content_plugin": "kanban",
        "board_id": _safe_text(snapshot.get("board_id"), limit=320),
        "revision": board_revision,
        "event_sequence": (
            snapshot.get("event_sequence")
            if isinstance(snapshot.get("event_sequence"), int)
            and not isinstance(snapshot.get("event_sequence"), bool)
            and snapshot.get("event_sequence") >= 0
            else None
        ),
        "columns": columns,
        "items": items,
    }


def _normalise_model(raw: Mapping[str, Any], catalog_revision: object, index: int) -> dict[str, Any]:
    result = {
        "kind": "model",
        "id": _safe_text(raw.get("id") or raw.get("model_id"), limit=200),
        "provider": _safe_text(raw.get("provider"), limit=120),
        "runtime": _safe_text(raw.get("runtime"), limit=120),
        "available": bool(raw.get("available")),
        "healthy": bool(raw.get("healthy")),
        "loaded": bool(raw.get("loaded")),
        "context_window": raw.get("context_window"),
        "quantization": _safe_text(raw.get("quantization"), limit=80),
        "capabilities": _safe_sequence(raw.get("capabilities"), limit=50),
        "default": bool(raw.get("default")),
        "revision": raw.get("revision", catalog_revision),
        "_flat_index": index,
    }
    if "capability_sources" in raw:
        result["capability_sources"] = _safe_sequence(raw.get("capability_sources"), limit=50)
    if "conflicts" in raw:
        result["conflicts"] = _safe_sequence(raw.get("conflicts"), limit=50)
    return result


def normalise_catalog(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    catalog_revision = snapshot.get("revision")
    models: list[dict[str, Any]] = []
    raw_models = snapshot.get("models")
    if not isinstance(raw_models, list):
        raw_models = []
    for raw_model in raw_models:
        if not isinstance(raw_model, Mapping):
            continue
        model = _normalise_model(raw_model, catalog_revision, len(models))
        if model["id"]:
            models.append(model)
    errors = snapshot.get("provider_errors")
    provider_errors = (
        [
            {
                "provider": _safe_text(item.get("provider"), limit=120),
                "code": _safe_text(item.get("code"), limit=80),
            }
            for item in errors
            if isinstance(item, Mapping)
        ]
        if isinstance(errors, list)
        else []
    )
    return {
        "_content_plugin": "models",
        "revision": catalog_revision,
        "models": models,
        "items": models,
        "provider_errors": provider_errors,
    }


def _result(
    section_id: str,
    payload: dict[str, Any],
    *,
    message: str = "",
) -> SectionLoadResult:
    state = PanelState.HEALTHY if payload.get("items") else PanelState.EMPTY
    return SectionLoadResult(section_id, state, payload, message)


class DashboardSurfaceController:
    def __init__(
        self,
        *,
        kanban_port: KanbanBackendPort | None = None,
        model_catalog_port: ModelCatalogPort | None = None,
        flags: DashboardFeatureFlags | None = None,
    ) -> None:
        self._kanban_port = kanban_port
        self._model_catalog_port = model_catalog_port
        self.flags = flags or DashboardFeatureFlags.from_mapping()
        self._payloads: dict[str, dict[str, Any]] = {}
        self._selected = {"kanban": 0, "models": 0}

    def register(self, registry: SectionAdapterRegistry) -> None:
        if self.flags.kanban:
            registry.register_async("kanban", self.load_kanban)
        if self.flags.models:
            registry.register_async("models", self.load_models)

    def selected_index(self, section_id: str) -> int:
        return self._selected.get(section_id, 0)

    def select(self, section_id: str, index: int) -> int:
        items = self._payloads.get(section_id, {}).get("items")
        maximum = max(0, len(items) - 1) if isinstance(items, list) else 0
        selected = max(0, min(maximum, int(index)))
        self._selected[section_id] = selected
        return selected

    def move_selection(self, section_id: str, delta: int) -> int:
        return self.select(section_id, self.selected_index(section_id) + int(delta))

    async def load_kanban(self, _section_id: str = "kanban") -> SectionLoadResult:
        if not self.flags.kanban:
            return SectionLoadResult("kanban", PanelState.UNAUTHORIZED, {}, "Kanban ist deaktiviert")
        if self._kanban_port is None:
            return SectionLoadResult(
                "kanban",
                PanelState.DEGRADED,
                {},
                "KanbanBackendPort ist nicht konfiguriert",
            )
        try:
            payload = normalise_board(await self._kanban_port.fetch_board())
        except PermissionError as exc:
            return SectionLoadResult("kanban", PanelState.UNAUTHORIZED, {}, _safe_text(exc))
        except Exception as exc:
            return SectionLoadResult("kanban", PanelState.DEGRADED, {}, _safe_text(exc))
        self._payloads["kanban"] = payload
        self.select("kanban", self.selected_index("kanban"))
        return _result("kanban", payload, message="Kanban aktualisiert")

    async def load_models(self, _section_id: str = "models") -> SectionLoadResult:
        if not self.flags.models:
            return SectionLoadResult("models", PanelState.UNAUTHORIZED, {}, "Model-Menue ist deaktiviert")
        if self._model_catalog_port is None:
            return SectionLoadResult(
                "models",
                PanelState.DEGRADED,
                {},
                "ModelCatalogPort ist nicht konfiguriert",
            )
        try:
            payload = normalise_catalog(await self._model_catalog_port.fetch_catalog())
        except PermissionError as exc:
            return SectionLoadResult("models", PanelState.UNAUTHORIZED, {}, _safe_text(exc))
        except Exception as exc:
            return SectionLoadResult("models", PanelState.DEGRADED, {}, _safe_text(exc))
        self._payloads["models"] = payload
        self.select("models", self.selected_index("models"))
        return _result("models", payload, message="Model-Catalog aktualisiert")

    def _selected_item(self, section_id: str) -> dict[str, Any] | None:
        items = self._payloads.get(section_id, {}).get("items")
        index = self.selected_index(section_id)
        if not isinstance(items, list) or not (0 <= index < len(items)):
            return None
        item = items[index]
        return item if isinstance(item, dict) else None

    async def activate_selected(self, section_id: str) -> SectionLoadResult:
        if section_id == "kanban":
            if "kanban" not in self._payloads:
                return await self.load_kanban()
            return _result("kanban", self._payloads["kanban"], message="Taskdetail ausgewaehlt")
        if section_id == "models":
            if "models" not in self._payloads:
                return await self.load_models()
            return _result("models", self._payloads["models"], message="Modelldetail ausgewaehlt")
        return SectionLoadResult(section_id, PanelState.DEGRADED, {}, "Unbekannte Dashboard-Sektion")

    async def activate_target(self, target: RegionTarget) -> SectionLoadResult:
        self.select(target.section_id, int(target.payload.get("selected_index") or 0))
        return await self.activate_selected(target.section_id)

    async def _reload_after_kanban_command(
        self,
        operation,
        *,
        success_message: str,
    ) -> SectionLoadResult:
        try:
            await operation
        except Exception as exc:
            if getattr(exc, "status_code", None) != 409:
                return SectionLoadResult("kanban", PanelState.DEGRADED, {}, _safe_text(exc))
            loaded = await self.load_kanban()
            payload = dict(loaded.payload)
            payload["revision_conflict"] = True
            self._payloads["kanban"] = payload
            return SectionLoadResult(
                "kanban",
                loaded.state,
                payload,
                "Revision Conflict: Snapshot neu geladen",
            )
        loaded = await self.load_kanban()
        return SectionLoadResult("kanban", loaded.state, loaded.payload, success_message)

    async def move_selected(
        self,
        target_status: str,
        *,
        target_position: int | None = None,
    ) -> SectionLoadResult:
        item = self._selected_item("kanban")
        if item is None or self._kanban_port is None:
            return SectionLoadResult("kanban", PanelState.EMPTY, {}, "Kein Task ausgewaehlt")
        operation = self._kanban_port.move_task(
            item["id"],
            target_status=_safe_text(target_status, limit=80),
            target_position=target_position,
            expected_revision=item.get("revision"),
        )
        return await self._reload_after_kanban_command(operation, success_message="Task verschoben")

    async def assign_selected(self, assignee_id: str) -> SectionLoadResult:
        item = self._selected_item("kanban")
        if item is None or self._kanban_port is None:
            return SectionLoadResult("kanban", PanelState.EMPTY, {}, "Kein Task ausgewaehlt")
        operation = self._kanban_port.assign_task(
            item["id"],
            assignee_id=_safe_text(assignee_id, limit=160),
            expected_revision=item.get("revision"),
        )
        return await self._reload_after_kanban_command(operation, success_message="Task zugewiesen")

    async def comment_selected(self, body: str) -> SectionLoadResult:
        item = self._selected_item("kanban")
        text = _safe_text(body, limit=2000)
        if item is None or self._kanban_port is None or not text:
            return SectionLoadResult("kanban", PanelState.EMPTY, {}, "Task und Kommentar erforderlich")
        operation = self._kanban_port.comment_task(
            item["id"],
            body=text,
            expected_revision=item.get("revision"),
        )
        return await self._reload_after_kanban_command(operation, success_message="Kommentar gespeichert")

    async def block_selected(self, reason: str) -> SectionLoadResult:
        item = self._selected_item("kanban")
        text = _safe_text(reason, limit=500)
        if item is None or self._kanban_port is None or not text:
            return SectionLoadResult("kanban", PanelState.EMPTY, {}, "Task und Blockergrund erforderlich")
        operation = self._kanban_port.block_task(
            item["id"],
            reason=text,
            expected_revision=item.get("revision"),
        )
        return await self._reload_after_kanban_command(operation, success_message="Task blockiert")

    async def complete_selected(self) -> SectionLoadResult:
        item = self._selected_item("kanban")
        if item is None or self._kanban_port is None:
            return SectionLoadResult("kanban", PanelState.EMPTY, {}, "Kein Task ausgewaehlt")
        operation = self._kanban_port.complete_task(
            item["id"],
            expected_revision=item.get("revision"),
        )
        return await self._reload_after_kanban_command(operation, success_message="Task abgeschlossen")

    async def refresh_models(self) -> SectionLoadResult:
        if self._model_catalog_port is None:
            return SectionLoadResult("models", PanelState.DEGRADED, {}, "ModelCatalogPort fehlt")
        try:
            await self._model_catalog_port.refresh_catalog()
        except Exception as exc:
            return SectionLoadResult("models", PanelState.DEGRADED, {}, _safe_text(exc))
        loaded = await self.load_models()
        return SectionLoadResult("models", loaded.state, loaded.payload, "Model-Catalog refreshed")

    async def set_selected_default(self) -> SectionLoadResult:
        item = self._selected_item("models")
        if item is None or self._model_catalog_port is None:
            return SectionLoadResult("models", PanelState.EMPTY, {}, "Kein Modell ausgewaehlt")
        try:
            await self._model_catalog_port.set_default(
                item["id"],
                expected_revision=item.get("revision"),
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 409:
                loaded = await self.load_models()
                return SectionLoadResult(
                    "models",
                    loaded.state,
                    loaded.payload,
                    "Revision Conflict: Model-Catalog neu geladen",
                )
            return SectionLoadResult("models", PanelState.DEGRADED, {}, _safe_text(exc))
        loaded = await self.load_models()
        return SectionLoadResult("models", loaded.state, loaded.payload, "Default-Modell gesetzt")

    async def handle_mouse(
        self,
        previous: MouseState | None,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
        event_type: str,
        region_index: RegionIndex,
        now: float | None = None,
    ) -> tuple[MouseState, RegionTarget | None, SectionLoadResult | None]:
        mouse = normalize_mouse_state(
            previous,
            x=x,
            y=y,
            width=width,
            height=height,
            event_type=event_type,  # type: ignore[arg-type]
            buttons=1 if event_type == "down" else 0,
            now=now,
        )
        target = region_index.get_target_at(mouse.x, mouse.y)
        if event_type != "down" or target is None or target.section_id not in {"kanban", "models"}:
            return mouse, target, None
        return mouse, target, await self.activate_target(target)


def _clip(value: object, width: int) -> str:
    text = _safe_text(value)
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "~"


class KanbanContentPlugin(ContentPlugin):
    @property
    def id(self) -> str:
        return "kanban"

    @property
    def label(self) -> str:
        return "Kanban"

    def render(self, payload: dict[str, Any], width: int, height: int, selected: int) -> list[str]:
        panel_state = str(payload.get("_panel_state") or "")
        if panel_state in {"loading", "unauthorized", "degraded"} and not payload.get("columns"):
            return [f"  {panel_state}"]
        columns = payload.get("columns")
        if not isinstance(columns, list) or not columns:
            return ["  Keine Tasks"]
        w = max(12, int(width))
        h = max(1, int(height))
        plan = plan_kanban_viewport(
            payload,
            width=w,
            height=h,
            selected_index=selected,
        )
        lines = [
            f"  layout:{plan.layout} revision:{payload.get('revision', '-')}"
        ]
        if plan.layout == "list":
            for slot in plan.list_slots:
                task = slot.task
                marker = ">" if slot.flat_index == selected else " "
                blocker = " BLOCKED" if task.get("blocked") else ""
                label = (
                    f"{marker} [{slot.column_title}] {task.get('title')} "
                    f"({task.get('priority') or '-'}){blocker}"
                )
                lines.append(_clip(label, w))
        else:
            visible_columns = plan.columns
            count = max(1, len(visible_columns))
            cell_width = max(12, (w - (count - 1) * 3) // count)
            lines.append(
                " | ".join(
                    _clip(column.title, cell_width)
                    for column in visible_columns
                )
            )
            row_count = max(
                (len(column.slots) for column in visible_columns),
                default=0,
            )
            for row in range(row_count):
                cells: list[str] = []
                for column in visible_columns:
                    slot = column.slots[row] if row < len(column.slots) else None
                    if slot is None:
                        cells.append("")
                    else:
                        task = slot.task
                        marker = ">" if slot.flat_index == selected else " "
                        blocker = " !" if task.get("blocked") else ""
                        cells.append(f"{marker}{task.get('title')}{blocker}")
                lines.append(" | ".join(_clip(cell, cell_width) for cell in cells))
        lines.append("  Enter=Detail; Aktionen via Hub: move assign comment block complete")
        return lines[:h]


class ModelCatalogContentPlugin(ContentPlugin):
    @property
    def id(self) -> str:
        return "models"

    @property
    def label(self) -> str:
        return "Models"

    def render(self, payload: dict[str, Any], width: int, height: int, selected: int) -> list[str]:
        panel_state = str(payload.get("_panel_state") or "")
        if panel_state in {"loading", "unauthorized", "degraded"} and not payload.get("models"):
            return [f"  {panel_state}"]
        models = payload.get("models")
        if not isinstance(models, list) or not models:
            return ["  Keine Modelle"]
        w = max(12, int(width))
        lines = [f"  catalog revision:{payload.get('revision', '-')}"]
        for index, model in enumerate(models):
            if not isinstance(model, dict):
                continue
            marker = ">" if index == selected else " "
            health = "healthy" if model.get("healthy") else "unhealthy"
            available = "available" if model.get("available") else "unavailable"
            default = " DEFAULT" if model.get("default") else ""
            sources = ",".join(model.get("capability_sources") or [])
            conflicts = ",".join(model.get("conflicts") or [])
            diagnostics = f" sources={sources}" if sources else ""
            diagnostics += f" conflicts={conflicts}" if conflicts else ""
            lines.append(
                _clip(
                    f"{marker} {model.get('provider')}/{model.get('id')} "
                    f"{health} {available}{default}{diagnostics}",
                    w,
                )
            )
        errors = payload.get("provider_errors") or []
        if errors:
            lines.append(f"  provider errors:{len(errors)}")
        lines.append("  Aktionen: refresh | set default (server allowlist)")
        return lines[: max(1, int(height))]


def dashboard_region_rects(
    section_id: str,
    payload: Mapping[str, Any],
    *,
    x1: int,
    x2: int,
    y1: int,
    y2: int,
    selected_index: int = 0,
) -> list[RegionRect]:
    if section_id not in {"kanban", "models"}:
        return []
    width = max(1, x2 - x1 + 1)
    regions: list[RegionRect] = []
    if section_id == "models":
        for index, model in enumerate(payload.get("models") or []):
            if not isinstance(model, Mapping):
                continue
            row = y1 + 2 + index
            if row > y2:
                break
            regions.append(
                RegionRect(
                    x1,
                    row,
                    x2,
                    row,
                    RegionTarget(
                        "dashboard_item",
                        "models",
                        "content",
                        _safe_text(model.get("id"), limit=200),
                        {"selected_index": index, "id": _safe_text(model.get("id"), limit=200)},
                    ),
                )
            )
        return regions

    plan = plan_kanban_viewport(
        payload,
        width=width,
        height=max(1, y2 - y1),
        selected_index=selected_index,
    )
    if plan.layout == "list":
        for visible_row, slot in enumerate(plan.list_slots):
            row = y1 + 2 + visible_row
            if row > y2:
                break
            task = slot.task
            regions.append(
                RegionRect(
                    x1,
                    row,
                    x2,
                    row,
                    RegionTarget(
                        "dashboard_item",
                        "kanban",
                        "content",
                        _safe_text(task.get("title"), limit=240),
                        {
                            "selected_index": slot.flat_index,
                            "id": _safe_text(task.get("id"), limit=160),
                        },
                    ),
                )
            )
        return regions

    visible_columns = plan.columns
    count = max(1, len(visible_columns))
    gap = 3
    cell_width = max(12, (width - (count - 1) * gap) // count)
    for column_index, column in enumerate(visible_columns):
        column_x1 = x1 + column_index * (cell_width + gap)
        column_x2 = min(x2, column_x1 + cell_width - 1)
        for row_index, slot in enumerate(column.slots):
            row = y1 + 3 + row_index
            if row > y2:
                break
            task = slot.task
            regions.append(
                RegionRect(
                    column_x1,
                    row,
                    column_x2,
                    row,
                    RegionTarget(
                        "dashboard_item",
                        "kanban",
                        "content",
                        _safe_text(task.get("title"), limit=240),
                        {
                            "selected_index": slot.flat_index,
                            "id": _safe_text(task.get("id"), limit=160),
                        },
                    ),
                )
            )
    return regions
