from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from client_surfaces.operator_tui.visual.runtime.frame_model import RenderScene
from client_surfaces.operator_tui.visual.views.base_view import ViewContext, ViewRequirements


def _is_allowed_path(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


@dataclass
class ArtifactPreviewView:
    view_id: str = "artifact_preview"

    def view_requirements(self) -> ViewRequirements:
        return ViewRequirements(
            view_id=self.view_id,
            display_name="Artifact Preview",
            description="Preview of selected artifact",
            required_render_features=("ansi",),
            optional_runtime_requirements=("artifact_source",),
        )

    def update(self, dt: float, state: dict[str, object]) -> None:
        _ = dt
        _ = state

    def render(self, context: ViewContext) -> RenderScene:
        descriptor = context.state.get("artifact")
        if not isinstance(descriptor, dict):
            return RenderScene(
                scene_type="artifact_preview",
                nodes=[{"kind": "placeholder", "text": "Kein Artefakt ausgewaehlt"}],
                metadata={"animated": False, "cache_hint": "static"},
            )

        path_text = str(descriptor.get("path") or "").strip()
        title = str(descriptor.get("title") or Path(path_text).name or "artifact").strip()
        kind = str(descriptor.get("kind") or "unknown").strip()
        allowed_roots_raw = context.state.get("allowed_roots")
        if isinstance(allowed_roots_raw, list):
            roots = [Path(str(item)).resolve() for item in allowed_roots_raw if str(item).strip()]
        else:
            roots = [Path.cwd().resolve()]

        nodes: list[dict[str, object]] = [
            {"kind": "label", "text": f"artifact: {title}", "x": 0, "y": 0},
            {"kind": "label", "text": f"type: {kind}", "x": 0, "y": 1},
        ]
        if path_text:
            path = Path(path_text).expanduser().resolve()
            if _is_allowed_path(path, roots):
                nodes.append({"kind": "label", "text": f"path: {path}", "x": 0, "y": 2})
            else:
                nodes.append({"kind": "error", "text": "Artefaktpfad ausserhalb erlaubter Roots", "x": 0, "y": 2})
        else:
            nodes.append({"kind": "placeholder", "text": "Kein Pfad vorhanden", "x": 0, "y": 2})
        return RenderScene(
            scene_type="artifact_preview",
            nodes=nodes,
            metadata={"animated": False, "cache_hint": "static"},
        )

