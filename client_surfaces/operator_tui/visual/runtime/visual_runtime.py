from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from client_surfaces.operator_tui.visual.adapters.base_output_adapter import DrawContext, DrawResult
from client_surfaces.operator_tui.visual.capabilities.models import TerminalVisualCapabilities
from client_surfaces.operator_tui.visual.renderers.base_renderer import RenderContext
from client_surfaces.operator_tui.visual.runtime.config import VisualViewportConfig
from client_surfaces.operator_tui.visual.runtime.fallback_resolver import resolve_renderer_adapter_pair
from client_surfaces.operator_tui.visual.runtime.frame_cache import (
    FrameBackpressureBuffer,
    FrameCache,
    FrameCacheKey,
)
from client_surfaces.operator_tui.visual.runtime.frame_model import RenderFrame, RenderScene
from client_surfaces.operator_tui.visual.runtime.frame_scheduler import FrameScheduler
from client_surfaces.operator_tui.visual.runtime.registry import (
    OutputAdapterRegistry,
    RendererRegistry,
    ViewRegistry,
)
from client_surfaces.operator_tui.visual.viewport.layout_contract import ViewportRegion
from client_surfaces.operator_tui.visual.views.base_view import ViewContext

# Image-capable renderers that should trigger prefer_image_mode selection
_IMAGE_RENDERERS = frozenset({"cpu_raster", "svg_raster_optional"})
# Image-capable adapters
_IMAGE_ADAPTERS = frozenset({"kitty", "sixel"})
# Views that benefit from image mode (have diagram_image nodes)
_IMAGE_CAPABLE_VIEWS = frozenset({"markdown_mermaid_document"})


@dataclass(frozen=True)
class VisualRuntimeStatus:
    active_view: str
    active_renderer: str
    active_adapter: str
    scheduler: dict[str, int]
    cache: dict[str, int]
    backpressure: dict[str, int]
    fallback_diagnostics: tuple[str, ...]
    runtime_errors: tuple[str, ...]


class VisualRuntime:
    def __init__(
        self,
        *,
        config: VisualViewportConfig,
        view_registry: ViewRegistry,
        renderer_registry: RendererRegistry,
        adapter_registry: OutputAdapterRegistry,
        capabilities: TerminalVisualCapabilities,
    ) -> None:
        self._config = config
        self._views = view_registry
        self._renderers = renderer_registry
        self._adapters = adapter_registry
        self._capabilities = capabilities
        self._scheduler = FrameScheduler(
            target_fps=config.target_fps,
            max_fps=config.max_fps,
            dirty_only=False,
        )
        self._cache = FrameCache(max_entries=64)
        self._backpressure = FrameBackpressureBuffer()
        self._runtime_errors: list[str] = []
        self._fallback_diagnostics: list[str] = []
        self._excluded_pairs: set[tuple[str, str]] = set()
        self._view_instances: dict[str, object] = {}
        self._active_view_id = config.default_view
        self._active_renderer_id = config.default_renderer
        self._active_adapter_id = config.default_output_adapter
        self._last_adapter_fail_reason: str = ""
        self._select_renderer_adapter()
        if not self._views.has(self._active_view_id):
            names = self._views.names()
            if not names:
                raise RuntimeError("visual runtime requires at least one registered view")
            self._active_view_id = names[0]

    def _prefer_image_mode(self, scene: RenderScene | None = None) -> bool:
        """Return True when the active view or scene benefits from image-capable renderer/adapter (TGFX-002)."""
        if self._active_view_id in _IMAGE_CAPABLE_VIEWS:
            return True
        if scene is not None and any(
            isinstance(n, dict) and n.get("kind") == "diagram_image"
            for n in (scene.nodes or [])
        ):
            return True
        return False

    def _select_renderer_adapter(self, *, prefer_image: bool = False) -> None:
        resolution = resolve_renderer_adapter_pair(
            config=self._config,
            capabilities=self._capabilities,
            available_renderers=set(self._renderers.names()),
            available_adapters=set(self._adapters.names()),
            excluded_pairs=self._excluded_pairs,
            prefer_image_mode=prefer_image,
        )
        self._active_renderer_id = resolution.renderer
        self._active_adapter_id = resolution.adapter
        self._fallback_diagnostics = list(resolution.diagnostics)

    def _view(self) -> object:
        instance = self._view_instances.get(self._active_view_id)
        if instance is not None:
            return instance
        created = self._views.create(self._active_view_id)
        self._view_instances[self._active_view_id] = created
        return created

    def _renderer(self) -> object:
        return self._renderers.create(self._active_renderer_id)

    def _adapter(self) -> object:
        return self._adapters.create(self._active_adapter_id)

    def available_views(self) -> tuple[str, ...]:
        return self._views.names()

    def get_view_instance(self, view_id: str) -> object | None:
        """Return the cached view instance for view_id, or None if not yet created."""
        return self._view_instances.get(str(view_id))

    def switch_view(self, view_id: str) -> bool:
        target = str(view_id or "").strip()
        if not target or not self._views.has(target):
            return False
        # Clear stale image output when switching views (TGFX-005)
        self._clear_image_adapter()
        self._active_view_id = target
        self._scheduler.mark_dirty()
        # Re-select renderer/adapter for the new view
        self._select_renderer_adapter(prefer_image=target in _IMAGE_CAPABLE_VIEWS)
        return True

    def _clear_image_adapter(self) -> None:
        """Tell Kitty/Sixel adapters to clear stale images (TGFX-005)."""
        try:
            adapter = self._adapter()
            if hasattr(adapter, "clear"):
                import sys
                adapter.clear(sys.stdout)
        except Exception:
            pass

    def _scene_to_diagnostic_frame(self, *, region: ViewportRegion, now: float, message: str) -> RenderFrame:
        scene = RenderScene(
            scene_type="renderer_diagnostics",
            nodes=[{"kind": "error", "text": message}],
            metadata={"fallback": True},
        )
        renderer = self._renderer()
        return renderer.render(
            scene,
            width=region.pixel_width,
            height=region.pixel_height,
            context=RenderContext(now=now, metadata={"fallback": True}),
        )

    def _plain_diagnostic_frame(self, *, region: ViewportRegion, now: float, message: str) -> RenderFrame:
        width = max(1, int(region.pixel_width))
        height = max(1, int(region.pixel_height))
        rows = [" " * width for _ in range(height)]
        rows[0] = "[renderer_diagnostics]".ljust(width)[:width]
        if height > 1:
            rows[1] = str(message or "render fallback unavailable").ljust(width)[:width]
        return RenderFrame(
            frame_type="ansi",
            width=width,
            height=height,
            payload=rows,
            mime_or_format="text/plain",
            timestamp=now,
            metadata={"fallback": True, "renderer": "plain_diagnostics"},
        )

    def render_frame(
        self,
        *,
        region: ViewportRegion,
        now: float | None = None,
        state: dict[str, Any] | None = None,
        force: bool = False,
    ) -> RenderFrame | None:
        t_now = float(now if now is not None else time.monotonic())
        if not self._scheduler.should_render(now=t_now, force=force):
            return None

        state_map = dict(state or {})

        # Select image-capable renderer/adapter for image-capable views (TGFX-002)
        prefer_image = self._active_view_id in _IMAGE_CAPABLE_VIEWS
        if prefer_image and self._active_adapter_id not in _IMAGE_ADAPTERS:
            self._select_renderer_adapter(prefer_image=True)

        cache_key = FrameCacheKey(
            view_id=self._active_view_id,
            renderer_id=self._active_renderer_id,
            width=region.pixel_width,
            height=region.pixel_height,
            state_version=str(state_map.get("visual_state_version") or ""),
            theme_version=str(state_map.get("theme_version") or ""),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            view = self._view()
            view.update(0.0, state_map)
            scene = view.render(ViewContext(region=region, now=t_now, state=state_map))
            renderer = self._renderer()
            frame = renderer.render(
                scene,
                width=region.pixel_width,
                height=region.pixel_height,
                context=RenderContext(now=t_now, metadata={"view": self._active_view_id}),
            )
            self._cache.put(cache_key, frame)
            return frame
        except Exception as exc:
            self._runtime_errors.append(f"render failed: {exc}")
            self._runtime_errors = self._runtime_errors[-20:]
            self._excluded_pairs.add((self._active_renderer_id, self._active_adapter_id))
            try:
                self._select_renderer_adapter()
            except RuntimeError as fallback_exc:
                self._runtime_errors.append(f"fallback failed: {fallback_exc}")
                self._runtime_errors = self._runtime_errors[-20:]
                return self._plain_diagnostic_frame(
                    region=region,
                    now=t_now,
                    message=f"render fallback unavailable: {exc}",
                )
            return self._scene_to_diagnostic_frame(
                region=region,
                now=t_now,
                message=f"render fallback: {exc}",
            )

    def draw(
        self,
        *,
        region: ViewportRegion,
        now: float | None = None,
        stream: Any,
        state: dict[str, Any] | None = None,
    ) -> DrawResult:
        t_now = float(now if now is not None else time.monotonic())
        if self._backpressure.has_pending():
            pending = self._backpressure.pop()
            if pending is not None:
                adapter = self._adapter()
                result = adapter.draw(
                    pending,
                    region=region,
                    stream=stream,
                    context=DrawContext(now=t_now, metadata={"from_backpressure": True}),
                )
                if result.drawn:
                    return result

        frame = self.render_frame(region=region, now=t_now, state=state, force=False)
        if frame is None:
            return DrawResult(drawn=False, reason="scheduler_skip")

        adapter = self._adapter()
        draw_result = adapter.draw(
            frame,
            region=region,
            stream=stream,
            context=DrawContext(now=t_now, metadata={"view": self._active_view_id}),
        )

        # TGFX-008: If image adapter fails at runtime, retry with next fallback
        if not draw_result.drawn:
            reason = str(draw_result.reason or "")
            # Adapter genuinely failed (not just busy/scheduler skip)
            if reason not in {"scheduler_skip", "busy", "disabled"}:
                pair = (self._active_renderer_id, self._active_adapter_id)
                if pair not in self._excluded_pairs:
                    self._excluded_pairs.add(pair)
                    self._last_adapter_fail_reason = f"{self._active_adapter_id}: {reason}"
                    self._fallback_diagnostics.append(
                        f"adapter draw failed ({reason}), retrying with next fallback"
                    )
                    try:
                        self._select_renderer_adapter()
                        # Re-render with new renderer/adapter
                        frame2 = self.render_frame(region=region, now=t_now, state=state, force=True)
                        if frame2 is not None:
                            adapter2 = self._adapter()
                            draw_result = adapter2.draw(
                                frame2, region=region, stream=stream,
                                context=DrawContext(now=t_now, metadata={"retry": True}),
                            )
                    except Exception as retry_exc:
                        self._runtime_errors.append(f"retry failed: {retry_exc}")
                        self._runtime_errors = self._runtime_errors[-20:]

        if not draw_result.drawn and str(draw_result.reason or "").lower() == "busy":
            is_animation = bool(frame.metadata.get("animated"))
            self._backpressure.offer(frame, is_animation=is_animation)
        return draw_result

    def status(self) -> VisualRuntimeStatus:
        scheduler_stats = self._scheduler.stats()
        cache_stats = self._cache.stats()
        backpressure_stats = self._backpressure.stats()
        return VisualRuntimeStatus(
            active_view=self._active_view_id,
            active_renderer=self._active_renderer_id,
            active_adapter=self._active_adapter_id,
            scheduler={
                "rendered_frames": scheduler_stats.rendered_frames,
                "skipped_frames": scheduler_stats.skipped_frames,
                "dropped_frames": scheduler_stats.dropped_frames,
            },
            cache={
                "hits": cache_stats.hits,
                "misses": cache_stats.misses,
                "evictions": cache_stats.evictions,
            },
            backpressure={
                "dropped_frames": backpressure_stats.dropped_frames,
                "queued_frames": backpressure_stats.queued_frames,
            },
            fallback_diagnostics=tuple(self._fallback_diagnostics),
            runtime_errors=tuple(self._runtime_errors),
        )
