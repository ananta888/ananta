from __future__ import annotations

from collections.abc import Callable

from client_surfaces.operator_tui.models import PanelState, SectionLoadResult
from client_surfaces.operator_tui.sections import get_section

SectionLoader = Callable[[str], SectionLoadResult]


class SectionAdapterRegistry:
    def __init__(self, loader: SectionLoader | None = None) -> None:
        self._loader = loader or self._fixture_loader

    def load(self, section_id: str) -> SectionLoadResult:
        section = get_section(section_id)
        try:
            return self._loader(section.id)
        except PermissionError as exc:
            return SectionLoadResult(section.id, PanelState.UNAUTHORIZED, {}, str(exc))
        except TimeoutError as exc:
            return SectionLoadResult(section.id, PanelState.DEGRADED, {}, str(exc) or "timed out")
        except Exception as exc:
            return SectionLoadResult(section.id, PanelState.DEGRADED, {}, str(exc))

    @staticmethod
    def _fixture_loader(section_id: str) -> SectionLoadResult:
        section = get_section(section_id)
        payload = {
            "title": section.title,
            "dependencies": list(section.primary_dependencies),
            "fallback": section.fallback,
            "loading_policy": "section_local",
            "render_policy": "partial_first_paint",
            "mutation_policy": "hub_dispatch_only",
        }
        if section.id == "goals":
            payload["items"] = [
                {"id": "G-1", "status": "in_progress", "title": "Operator TUI rollout"},
                {"id": "G-2", "status": "todo", "title": "Diagram rendering hardening"},
            ]
            return SectionLoadResult(section.id, PanelState.HEALTHY, payload, "loaded")
        if section.id == "tasks":
            payload["items"] = [
                {"id": "TUI-T26", "status": "done", "agent": "hub", "title": "Read-only operator flows"},
                {"id": "TUI-T27", "status": "done", "agent": "hub", "title": "Explicit action dispatch"},
            ]
            payload["timeline"] = [
                {"id": "TL-1", "status": "loaded", "summary": "fixture timeline available"},
            ]
            return SectionLoadResult(section.id, PanelState.HEALTHY, payload, "loaded")
        if section.id in {"artifacts", "knowledge", "audit"}:
            payload["items"] = []
            return SectionLoadResult(section.id, PanelState.EMPTY, payload, "empty")
        return SectionLoadResult(section.id, PanelState.HEALTHY, payload, "loaded")


def merge_section_result(state_payloads: dict[str, dict] | None, result: SectionLoadResult) -> dict[str, dict]:
    payloads = dict(state_payloads or {})
    payloads[result.section_id] = dict(result.payload)
    return payloads


def merge_panel_state(panel_states: dict[str, PanelState] | None, result: SectionLoadResult) -> dict[str, PanelState]:
    states = dict(panel_states or {})
    states[result.section_id] = result.state
    return states
