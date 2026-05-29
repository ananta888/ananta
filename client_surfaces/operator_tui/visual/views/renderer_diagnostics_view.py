from __future__ import annotations

from dataclasses import dataclass

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext, ViewRequirements


def _ok(v: bool) -> str:
    return "✓" if v else "✗"


def _cap_line(label: str, available: bool, reason: str = "") -> str:
    status = "✓ ok" if available else f"✗ {reason}" if reason else "✗"
    return f"  {label:<22} {status}"


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

        lines: list[str] = [
            "── Renderer Selection ──────────────────────",
            f"  view:             {view}",
            f"  renderer:         {renderer}",
            f"  adapter:          {adapter}",
            f"  viewport:         {context.region.columns}×{context.region.rows} ({context.region.pixel_width}×{context.region.pixel_height}px)",
            f"  fps:              {fps}",
            f"  fallback reason:  {fallback}",
            "",
        ]

        # Image output capabilities (TGFX-004)
        try:
            from client_surfaces.operator_tui.visual.capabilities.terminal_detector import detect_image_output_capabilities
            caps = detect_image_output_capabilities()
            lines += [
                "── Image Output Capabilities ───────────────",
                _cap_line("mmdc (mermaid-cli)", caps.mermaid_renderer.mmdc_available,
                          "install: npm install -g @mermaid-js/mermaid-cli" if not caps.mermaid_renderer.mmdc_available else ""),
                _cap_line("playwright", caps.mermaid_renderer.playwright_available,
                          "pip install playwright" if not caps.mermaid_renderer.playwright_available else ""),
                _cap_line("Pillow (raster)", caps.raster_renderer.pillow_available,
                          "pip install Pillow" if not caps.raster_renderer.pillow_available else ""),
                _cap_line("cairosvg (SVG→PNG)", caps.raster_renderer.cairosvg_available,
                          "pip install cairosvg" if not caps.raster_renderer.cairosvg_available else ""),
                _cap_line("Kitty protocol", caps.kitty_supported,
                          "use Kitty/WezTerm or ANANTA_FORCE_KITTY=1" if not caps.kitty_supported else ""),
                _cap_line("Sixel protocol", caps.sixel_supported,
                          "SIXEL_SUPPORTED=1 or ANANTA_FORCE_SIXEL=1" if not caps.sixel_supported else ""),
                "",
                f"  can_show_mermaid_image: {_ok(caps.can_show_mermaid_image())}",
            ]
            degraded = caps.degraded_reasons()
            if degraded:
                lines.append("  degraded:")
                for r in degraded[:4]:
                    lines.append(f"    · {r[:60]}")
        except Exception as e:
            lines.append(f"  capabilities: error ({e})")

        lines.append("")

        # Fallback diagnostics chain
        fallback_diags = context.state.get("fallback_diagnostics")
        if isinstance(fallback_diags, (list, tuple)) and fallback_diags:
            lines += ["── Resolver Diagnostics ────────────────────"]
            for d in list(fallback_diags)[-6:]:
                lines.append(f"  {str(d)[:64]}")
            lines.append("")

        # Runtime errors
        rt_errors = context.state.get("runtime_errors")
        if isinstance(rt_errors, (list, tuple)) and rt_errors:
            lines += ["── Runtime Errors ──────────────────────────"]
            for e in list(rt_errors)[-4:]:
                lines.append(f"  {str(e)[:64]}")
            lines.append("")

        # Chat backend info
        mem_status = context.state.get("last_chat_memory_status")
        chat_debug = bool(context.state.get("chat_memory_debug"))
        if chat_debug and isinstance(mem_status, dict):
            lines += ["── Chat Memory ─────────────────────────────"]
            lines.append(f"  history={mem_status.get('history_used')}")
            lines.append(f"  summary={mem_status.get('summary_used')}")
            lines.append(f"  codecompass={mem_status.get('codecompass_used')}")
            lines.append(f"  rag_count={mem_status.get('rag_count', 0)}")
            backend = str(context.state.get("last_chat_backend_path") or "-")
            latency = str(context.state.get("last_chat_latency_ms") or "-")
            lines.append(f"  backend={backend} ({latency}ms)")
        elif isinstance(mem_status, dict):
            backend = str(context.state.get("last_chat_backend_path") or "-")
            latency = str(context.state.get("last_chat_latency_ms") or "-")
            lines.append(f"  chat_backend={backend} {latency}ms")

        nodes: list[dict[str, object]] = [
            {"kind": "label", "text": line, "x": 0, "y": y}
            for y, line in enumerate(lines)
        ]
        return RenderScene(
            scene_type="renderer_diagnostics",
            nodes=nodes,
            metadata={"animated": False, "cache_hint": "state_versioned"},
        )
