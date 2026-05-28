from __future__ import annotations

from dataclasses import dataclass
from io import StringIO

from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawResult
from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities
from client_surfaces.operator_tui.visual.runtime.config import VisualViewportConfig
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene
from client_surfaces.operator_tui.visual.runtime.registry import OutputAdapterRegistry, RendererRegistry, ViewRegistry
from client_surfaces.operator_tui.visual.runtime.visual_runtime import VisualRuntime
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion


@dataclass
class _SimpleView:
    view_id: str

    def update(self, dt: float, state: dict[str, object]) -> None:
        _ = dt
        _ = state

    def render(self, context) -> RenderScene:
        _ = context
        return RenderScene(
            scene_type=self.view_id,
            nodes=[{"kind": "text", "text": self.view_id}],
            metadata={"animated": False},
        )


@dataclass
class _RendererOk:
    renderer_id: str

    def render(self, scene: RenderScene, *, width: int, height: int, context) -> RenderFrame:
        _ = context
        return RenderFrame(
            frame_type="ansi",
            width=width,
            height=height,
            payload=[scene.scene_type],
            mime_or_format="text/plain",
            timestamp=1.0,
            metadata={"animated": bool(scene.metadata.get("animated"))},
        )


@dataclass
class _RendererBroken:
    renderer_id: str

    def render(self, scene: RenderScene, *, width: int, height: int, context) -> RenderFrame:
        _ = scene
        _ = width
        _ = height
        _ = context
        raise RuntimeError("renderer boom")


@dataclass
class _AdapterOk:
    adapter_id: str

    def draw(self, frame: RenderFrame, *, region: ViewportRegion, stream, context) -> DrawResult:
        _ = region
        _ = context
        stream.write(str(frame.payload))
        return DrawResult(drawn=True, reason="ok")


def _region() -> ViewportRegion:
    return ViewportRegion(x=24, y=9, columns=50, rows=16, pixel_width=800, pixel_height=450)


def test_visual_runtime_switches_views_without_restart() -> None:
    views = ViewRegistry()
    renderers = RendererRegistry()
    adapters = OutputAdapterRegistry()
    views.register_factory("logo_animation", lambda: _SimpleView("logo_animation"))
    views.register_factory("snake_debug_view", lambda: _SimpleView("snake_debug_view"))
    renderers.register_factory("ansi_blocks", lambda: _RendererOk("ansi_blocks"))
    adapters.register_factory("ansi", lambda: _AdapterOk("ansi"))
    runtime = VisualRuntime(
        config=VisualViewportConfig(
            default_view="logo_animation",
            default_renderer="ansi_blocks",
            default_output_adapter="ansi",
        ),
        view_registry=views,
        renderer_registry=renderers,
        adapter_registry=adapters,
        capabilities=TerminalVisualCapabilities(ansi=True),
    )
    assert runtime.switch_view("snake_debug_view") is True
    out = StringIO()
    result = runtime.draw(region=_region(), stream=out, now=1.0, state={})
    assert result.drawn is True
    assert "snake_debug_view" in out.getvalue()


def test_visual_runtime_falls_back_when_renderer_fails() -> None:
    views = ViewRegistry()
    renderers = RendererRegistry()
    adapters = OutputAdapterRegistry()
    views.register_factory("logo_animation", lambda: _SimpleView("logo_animation"))
    renderers.register_factory("broken", lambda: _RendererBroken("broken"))
    renderers.register_factory("ansi_blocks", lambda: _RendererOk("ansi_blocks"))
    adapters.register_factory("ansi", lambda: _AdapterOk("ansi"))
    cfg = VisualViewportConfig(
        default_view="logo_animation",
        default_renderer="broken",
        default_output_adapter="ansi",
    )
    runtime = VisualRuntime(
        config=cfg,
        view_registry=views,
        renderer_registry=renderers,
        adapter_registry=adapters,
        capabilities=TerminalVisualCapabilities(ansi=True),
    )
    out = StringIO()
    result = runtime.draw(region=_region(), stream=out, now=1.0, state={})
    assert result.drawn is True
    status = runtime.status()
    assert status.active_renderer == "ansi_blocks"
    assert any("render failed" in row for row in status.runtime_errors)


def test_visual_runtime_returns_diagnostic_frame_when_last_renderer_fails() -> None:
    views = ViewRegistry()
    renderers = RendererRegistry()
    adapters = OutputAdapterRegistry()
    views.register_factory("logo_animation", lambda: _SimpleView("logo_animation"))
    renderers.register_factory("ansi_blocks", lambda: _RendererBroken("ansi_blocks"))
    adapters.register_factory("ansi", lambda: _AdapterOk("ansi"))
    runtime = VisualRuntime(
        config=VisualViewportConfig(
            default_view="logo_animation",
            default_renderer="ansi_blocks",
            default_output_adapter="ansi",
        ),
        view_registry=views,
        renderer_registry=renderers,
        adapter_registry=adapters,
        capabilities=TerminalVisualCapabilities(ansi=True),
    )

    frame = runtime.render_frame(region=_region(), now=1.0, state={}, force=True)

    assert frame is not None
    assert frame.frame_type == "ansi"
    assert frame.metadata["renderer"] == "plain_diagnostics"
    assert any("fallback failed" in row for row in runtime.status().runtime_errors)
