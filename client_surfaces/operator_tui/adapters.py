from __future__ import annotations

from collections.abc import Callable

from client_surfaces.operator_tui.models import PanelState, SectionLoadResult
from client_surfaces.operator_tui.sections import get_section

SectionLoader = Callable[[str], SectionLoadResult]


class SectionAdapterRegistry:
    def __init__(
        self,
        loader: SectionLoader | None = None,
        *,
        endpoint: str = "",
        token: str = "",
    ) -> None:
        self._loader = loader
        self._endpoint = str(endpoint or "").strip().rstrip("/")
        self._token = str(token or "").strip()
        self._use_hub = bool(self._endpoint) and loader is None

    def load(self, section_id: str) -> SectionLoadResult:
        section = get_section(section_id)

        if self._use_hub:
            try:
                from client_surfaces.operator_tui.hub_loader import fetch_hub_section
                result = fetch_hub_section(
                    section_id, self._endpoint, self._token, timeout=section.timeout_seconds
                )
                if result is not None:
                    return result
                return SectionLoadResult(section.id, PanelState.EMPTY, {}, "")
            except PermissionError as exc:
                return SectionLoadResult(section.id, PanelState.UNAUTHORIZED, {}, str(exc))
            except (TimeoutError, OSError) as exc:
                return SectionLoadResult(section.id, PanelState.DEGRADED, {}, f"Hub nicht erreichbar: {exc}")
            except Exception as exc:
                return SectionLoadResult(section.id, PanelState.DEGRADED, {}, f"Hub-Fehler: {exc}")

        if self._loader is not None:
            try:
                return self._loader(section.id)
            except PermissionError as exc:
                return SectionLoadResult(section.id, PanelState.UNAUTHORIZED, {}, str(exc))
            except TimeoutError as exc:
                return SectionLoadResult(section.id, PanelState.DEGRADED, {}, str(exc) or "timed out")
            except Exception as exc:
                return SectionLoadResult(section.id, PanelState.DEGRADED, {}, str(exc))

        return SectionLoadResult(section.id, PanelState.EMPTY, {}, "kein Hub konfiguriert")


def merge_section_result(state_payloads: dict[str, dict] | None, result: SectionLoadResult) -> dict[str, dict]:
    payloads = dict(state_payloads or {})
    payloads[result.section_id] = dict(result.payload)
    return payloads


def merge_panel_state(panel_states: dict[str, PanelState] | None, result: SectionLoadResult) -> dict[str, PanelState]:
    states = dict(panel_states or {})
    states[result.section_id] = result.state
    return states
