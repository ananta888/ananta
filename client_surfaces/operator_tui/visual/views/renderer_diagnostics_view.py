from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext, ViewRequirements


@dataclass
class RendererDiagnosticsView:
    view_id: str = "renderer_diagnostics"

    def view_requirements(self) -> ViewRequirements:
        return ViewRequirements(
            view_id=self.view_id,
            display_name="Renderer Diagnostics",
            description="Runtime renderer and adapter diagnostics",
            required_render_features=("ansi",),
            optional_runtime_requirements=(),
        )

    def update(self, dt: float, state: dict[str, object]) -> None:
        _ = dt
        _ = state

    def render(self, context: ViewContext) -> RenderScene:
        runtime = context.state.get("runtime_status") if isinstance(context.state.get("runtime_status"), dict) else {}
        renderer = str(runtime.get("active_renderer") or context.state.get("active_renderer") or "-")
        adapter = str(runtime.get("active_adapter") or context.state.get("active_adapter") or "-")
        view = str(runtime.get("active_view") or context.state.get("active_view") or self.view_id)
        fps = str(runtime.get("fps") or context.state.get("fps") or "-")
        fallback = str(runtime.get("fallback_reason") or context.state.get("fallback_reason") or "-")
        nodes: list[dict[str, object]] = [
            {"kind": "label", "text": f"view={view}", "x": 0, "y": 0},
            {"kind": "label", "text": f"renderer={renderer}", "x": 0, "y": 1},
            {"kind": "label", "text": f"adapter={adapter}", "x": 0, "y": 2},
            {"kind": "label", "text": f"fps={fps}", "x": 0, "y": 3},
            {
                "kind": "label",
                "text": f"viewport={context.region.columns}x{context.region.rows}",
                "x": 0,
                "y": 4,
            },
            {
                "kind": "label",
                "text": f"pixels={context.region.pixel_width}x{context.region.pixel_height}",
                "x": 0,
                "y": 5,
            },
            {"kind": "label", "text": f"fallback={fallback}", "x": 0, "y": 6},
        ]

        # CMW-014: chat memory/prompt debug when enabled
        mem_status = context.state.get("last_chat_memory_status")
        chat_debug = bool(context.state.get("chat_memory_debug"))
        if chat_debug and isinstance(mem_status, dict):
            y = 8
            nodes.append({"kind": "label", "text": "── chat memory ──", "x": 0, "y": y}); y += 1
            nodes.append({"kind": "label", "text": f"history={mem_status.get('history_used')}", "x": 0, "y": y}); y += 1
            nodes.append({"kind": "label", "text": f"summary={mem_status.get('summary_used')}", "x": 0, "y": y}); y += 1
            nodes.append({"kind": "label", "text": f"codecompass={mem_status.get('codecompass_used')}", "x": 0, "y": y}); y += 1
            nodes.append({"kind": "label", "text": f"rag_count={mem_status.get('rag_count', 0)}", "x": 0, "y": y}); y += 1
            backend = str(context.state.get("last_chat_backend_path") or "-")
            latency = str(context.state.get("last_chat_latency_ms") or "-")
            nodes.append({"kind": "label", "text": f"backend={backend} ({latency}ms)", "x": 0, "y": y}); y += 1
            fb_reason = str(context.state.get("last_chat_fallback_reason") or "")
            if fb_reason:
                nodes.append({"kind": "label", "text": f"fallback_reason={fb_reason[:40]}", "x": 0, "y": y})
        elif isinstance(mem_status, dict):
            backend = str(context.state.get("last_chat_backend_path") or "-")
            latency = str(context.state.get("last_chat_latency_ms") or "-")
            nodes.append({"kind": "label", "text": f"chat_backend={backend} {latency}ms", "x": 0, "y": 8})

        return RenderScene(
            scene_type="renderer_diagnostics",
            nodes=nodes,
            metadata={"animated": False, "cache_hint": "state_versioned"},
        )
