"""Deterministic diagram-notation renderer.

Renders Mermaid and BPMN 2.0 diagram source from structured payloads.

Design contract (NOT-001): deterministic, pure, safe, auditable.
See notation_renderer_common.py for shared primitives and the full
module docstring.

Supported patterns:
* Mermaid: ``mermaid.class``, ``mermaid.sequence``, ``mermaid.state``,
  ``mermaid.usecase``, ``mermaid.activity``
* BPMN 2.0: ``bpmn.process``, ``bpmn.pool_lane``, ``bpmn.collaboration``
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

from agent.services.notation_bpmn import (
    _render_bpmn_collaboration,
    _render_bpmn_pool_lane,
    _render_bpmn_process,
)
from agent.services.notation_mermaid_activity import _render_mermaid_activity
from agent.services.notation_mermaid_class import _render_mermaid_class
from agent.services.notation_mermaid_sequence import _render_mermaid_sequence
from agent.services.notation_mermaid_state import _render_mermaid_state
from agent.services.notation_mermaid_usecase import _render_mermaid_usecase
from agent.services.notation_renderer_common import (
    NotationArtifact,
    NotationRenderError,
    _as_str,
)

# Re-export public API so existing callers continue to work unchanged.
__all__ = [
    "NotationArtifact",
    "NotationRenderError",
    "NotationRenderer",
    "get_notation_renderer",
    "reset_notation_renderer_singleton",
]

_PATTERN_TO_GENERATOR = {
    "mermaid.class": _render_mermaid_class,
    "mermaid.sequence": _render_mermaid_sequence,
    "mermaid.state": _render_mermaid_state,
    "mermaid.usecase": _render_mermaid_usecase,
    "mermaid.activity": _render_mermaid_activity,
    "bpmn.process": _render_bpmn_process,
    "bpmn.pool_lane": _render_bpmn_pool_lane,
    "bpmn.collaboration": _render_bpmn_collaboration,
}

_PATTERN_TO_FILENAME = {
    "mermaid.class": "diagram.mmd",
    "mermaid.sequence": "diagram.mmd",
    "mermaid.state": "diagram.mmd",
    "mermaid.usecase": "diagram.mmd",
    "mermaid.activity": "diagram.mmd",
    "bpmn.process": "process.bpmn",
    "bpmn.pool_lane": "process.bpmn",
    "bpmn.collaboration": "collaboration.bpmn",
}


class NotationRenderer:
    """Render diagram notation patterns (Mermaid / BPMN 2.0).

    Stateless and safe to share across threads.
    """

    def render(
        self,
        *,
        pattern_plan: dict[str, Any],
        target_root: str | None = None,
    ) -> NotationArtifact:
        """Render a single notation pattern plan.

        Args:
            pattern_plan: validated pattern dict with ``pattern_id``,
                ``language`` and ``parameters`` (flat dict).
            target_root: directory to write the rendered file to.
                When ``None`` the renderer runs in dry-run mode.

        Returns:
            A :class:`NotationArtifact` with stable sha256 hashes.

        Raises:
            NotationRenderError: on validation errors, unknown
                pattern_id, or unsafe output paths.
        """
        pattern_id = _as_str(pattern_plan.get("pattern_id"),
                             field="pattern_plan.pattern_id")
        generator = _PATTERN_TO_GENERATOR.get(pattern_id)
        if generator is None:
            raise NotationRenderError(
                f"unknown notation pattern_id {pattern_id!r}; expected one of "
                f"{sorted(_PATTERN_TO_GENERATOR)}"
            )

        language = _as_str(pattern_plan.get("language"),
                           field="pattern_plan.language")
        if language not in {"mermaid", "bpmn"}:
            raise NotationRenderError(
                f"notation pattern language must be 'mermaid' or 'bpmn', "
                f"got {language!r}"
            )

        params = self._resolve_params(pattern_plan)
        source, default_filename = generator(params)

        output_filename = _PATTERN_TO_FILENAME.get(pattern_id, default_filename)
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        artifact = NotationArtifact(
            pattern_id=pattern_id,
            language=language,
            source=source,
            sha256=sha,
            bytes_written=len(source.encode("utf-8")),
            output_filename=output_filename,
        )

        if target_root:
            self._write_to_disk(artifact, target_root)
        return artifact

    @staticmethod
    def _resolve_params(pattern_plan: dict[str, Any]) -> dict[str, Any]:
        """Resolve parameters: ``parameters_provided`` > flat ``parameters``."""
        flat = pattern_plan.get("parameters_provided")
        if flat is None:
            parameters = pattern_plan.get("parameters")
            if isinstance(parameters, dict):
                flat = parameters
            elif isinstance(parameters, list):
                flat = {}
            else:
                raise NotationRenderError(
                    "pattern_plan parameters must be a dict (flat) or "
                    "a list (schema array)"
                )
        if not isinstance(flat, dict):
            raise NotationRenderError("pattern_plan parameters must be a dict")
        implicit = {}
        for implicit_key in ("pattern_id", "language"):
            val = pattern_plan.get(implicit_key)
            if val is not None:
                implicit[implicit_key] = val
        return {**flat, **implicit}

    @staticmethod
    def _write_to_disk(artifact: NotationArtifact, target_root: str) -> None:
        if os.path.isabs(artifact.output_filename):
            raise NotationRenderError(
                f"output_filename {artifact.output_filename!r} must be relative"
            )
        normalised = os.path.normpath(artifact.output_filename)
        if normalised.startswith("..") or "/.." in f"/{normalised}" or normalised == "..":
            raise NotationRenderError(
                f"output_filename {artifact.output_filename!r} escapes "
                f"the target root"
            )
        full = os.path.abspath(os.path.join(target_root, normalised))
        root_abs = os.path.abspath(target_root) + os.sep
        if not (full + os.sep).startswith(root_abs):
            raise NotationRenderError(
                f"output_filename {artifact.output_filename!r} resolves "
                f"outside target_root"
            )
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(artifact.source)
        with open(full, "rb") as f:
            actual_sha = hashlib.sha256(f.read()).hexdigest()
        if actual_sha != artifact.sha256:
            raise NotationRenderError(
                f"hash mismatch for {artifact.output_filename!r} after write"
            )


_default_renderer: NotationRenderer | None = None


def get_notation_renderer() -> NotationRenderer:
    """Return the shared renderer (stateless, safe to share)."""
    global _default_renderer
    if _default_renderer is None:
        _default_renderer = NotationRenderer()
    return _default_renderer


def reset_notation_renderer_singleton() -> None:
    """Test helper to drop the cached singleton."""
    global _default_renderer
    _default_renderer = None
