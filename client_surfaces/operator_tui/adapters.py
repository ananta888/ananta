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
        self._loader = loader or self._fixture_loader
        self._endpoint = str(endpoint or "").strip().rstrip("/")
        self._token = str(token or "").strip()
        # Use hub when endpoint is provided and no custom loader overrides it
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
            except PermissionError as exc:
                return SectionLoadResult(section.id, PanelState.UNAUTHORIZED, {}, str(exc))
            except (TimeoutError, OSError):
                pass  # hub unreachable → fixture fallback
            except Exception:
                pass  # any other hub error → fixture fallback
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
        payload: dict[str, object] = {
            "title": section.title,
            "dependencies": list(section.primary_dependencies),
            "fallback": section.fallback,
            "loading_policy": "section_local",
            "render_policy": "partial_first_paint",
            "mutation_policy": "hub_dispatch_only",
        }
        if section.id == "dashboard":
            payload["agents"] = {"online": 7, "total": 8}
            payload["llm_providers"] = {
                "claude-sonnet-4-6": "ok",
                "codex-2": "ok",
                "whisper-v3": "ok",
            }
            payload["queue"] = {"depth": 3}
            payload["goal_summary"] = "2 running · 5 done · 0 failed"
            payload["task_summary"] = "3 active · 12 completed"
            return SectionLoadResult(section.id, PanelState.HEALTHY, payload, "loaded")
        if section.id == "goals":
            payload["items"] = [
                {"id": "g-001", "status": "running", "title": "WebSocket live updates"},
                {"id": "g-002", "status": "running", "title": "Voice command recognition"},
                {"id": "g-003", "status": "done",    "title": "Refactor auth middleware"},
                {"id": "g-004", "status": "done",    "title": "Multi-agent orchestrator"},
                {"id": "g-005", "status": "done",    "title": "SVG logo 3D renderer"},
                {"id": "g-006", "status": "blocked", "title": "External API rate limit fix"},
            ]
            return SectionLoadResult(section.id, PanelState.HEALTHY, payload, "loaded")
        if section.id == "tasks":
            payload["items"] = [
                {"id": "t-0a1", "status": "running", "agent": "claude", "title": "Auth flow unit tests"},
                {"id": "t-0a2", "status": "running", "agent": "claude", "title": "Generate OpenAPI spec"},
                {"id": "t-0a3", "status": "running", "agent": "codex",  "title": "Refactor connection pool"},
                {"id": "t-0a4", "status": "done",    "agent": "claude", "title": "Fix ANSI rendering bug"},
                {"id": "t-0a5", "status": "done",    "agent": "claude", "title": "Add 3D SVG logo animation"},
                {"id": "t-0a6", "status": "done",    "agent": "codex",  "title": "Deploy landing page"},
            ]
            payload["timeline"] = [
                {"id": "t-0a4", "summary": "ANSI strip fix committed"},
                {"id": "t-0a5", "summary": "SVG logo animates in 3D"},
                {"id": "t-0a6", "summary": "www.ananta.de is live"},
            ]
            return SectionLoadResult(section.id, PanelState.HEALTHY, payload, "loaded")
        if section.id == "system":
            payload["agents"] = {"online": 7, "total": 8}
            payload["llm_providers"] = {
                "claude-sonnet-4-6": "ok  284ms",
                "codex-2":           "ok  110ms",
                "whisper-v3":        "ok  450ms",
            }
            payload["queue"] = {"depth": 3, "counts": {"pending": 3, "retry": 0}}
            payload["contracts"] = ["hub v2.1.4", "codex v1.0.3", "voice v0.4.1", "web v3.2.0"]
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
