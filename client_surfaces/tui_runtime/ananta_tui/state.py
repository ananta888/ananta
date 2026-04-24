from __future__ import annotations

from dataclasses import dataclass, field, replace
from time import time

from client_surfaces.tui_runtime.ananta_tui.surface_map import TUI_SECTION_ORDER


@dataclass(frozen=True)
class TuiViewState:
    current_section: str = "Dashboard"
    selected_goal_id: str | None = None
    selected_task_id: str | None = None
    selected_artifact_id: str | None = None
    selected_collection_id: str | None = None
    selected_template_id: str | None = None
    filters: dict[str, str] = field(default_factory=dict)
    refresh_count: int = 0
    last_refresh_epoch: float = 0.0
    compact_mode: bool = False

    def with_section(self, section: str) -> "TuiViewState":
        candidate = section.strip().title() if section else self.current_section
        if candidate not in TUI_SECTION_ORDER:
            candidate = self.current_section
        return replace(self, current_section=candidate)

    def with_filter(self, name: str, value: str) -> "TuiViewState":
        normalized_name = str(name or "").strip().lower()
        if not normalized_name:
            return self
        next_filters = dict(self.filters)
        next_filters[normalized_name] = str(value or "").strip()
        return replace(self, filters=next_filters)

    def with_selection(
        self,
        *,
        goal_id: str | None = None,
        task_id: str | None = None,
        artifact_id: str | None = None,
        collection_id: str | None = None,
        template_id: str | None = None,
    ) -> "TuiViewState":
        return replace(
            self,
            selected_goal_id=goal_id if goal_id is not None else self.selected_goal_id,
            selected_task_id=task_id if task_id is not None else self.selected_task_id,
            selected_artifact_id=artifact_id if artifact_id is not None else self.selected_artifact_id,
            selected_collection_id=collection_id if collection_id is not None else self.selected_collection_id,
            selected_template_id=template_id if template_id is not None else self.selected_template_id,
        )

    def mark_refresh(self) -> "TuiViewState":
        return replace(self, refresh_count=self.refresh_count + 1, last_refresh_epoch=time())

    def with_terminal_width(self, width: int) -> "TuiViewState":
        return replace(self, compact_mode=width < 100)

    def sanitize_selection(
        self,
        *,
        goal_ids: set[str],
        task_ids: set[str],
        artifact_ids: set[str],
        collection_ids: set[str],
        template_ids: set[str],
    ) -> "TuiViewState":
        return replace(
            self,
            selected_goal_id=self.selected_goal_id if self.selected_goal_id in goal_ids else None,
            selected_task_id=self.selected_task_id if self.selected_task_id in task_ids else None,
            selected_artifact_id=self.selected_artifact_id if self.selected_artifact_id in artifact_ids else None,
            selected_collection_id=self.selected_collection_id
            if self.selected_collection_id in collection_ids
            else None,
            selected_template_id=self.selected_template_id if self.selected_template_id in template_ids else None,
        )
