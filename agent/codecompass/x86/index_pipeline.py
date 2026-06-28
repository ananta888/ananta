"""X86CC-015: Pipeline for building x86 indexes from multiple inputs."""
from __future__ import annotations

from typing import Any

from agent.codecompass.x86.adapter import X86DisassemblerAdapter
from agent.codecompass.x86.config import X86Config
from agent.codecompass.x86.diagnostics import ADAPTER_ERROR, x86_diag_dict
from agent.codecompass.x86.index_builder import X86IndexBuilder, X86IndexLimits
from agent.codecompass.x86.input_taxonomy import X86InputRecord


class X86IndexPipeline:
    """Runs x86 indexing over a list of X86InputRecord objects.

    When the feature is disabled, returns empty dict (unchanged existing behavior).
    Adapter errors degrade only the x86 part — other indexing is not affected.
    """

    def __init__(self, builder: X86IndexBuilder | None = None) -> None:
        self._builder = builder or X86IndexBuilder()

    def run(
        self,
        inputs: list[X86InputRecord],
        config: X86Config,
        adapter: X86DisassemblerAdapter,
    ) -> dict[str, Any]:
        """Run the pipeline.

        Returns:
          dict with x86_nodes, x86_edges, x86_diagnostics, x86_manifest_list
          (empty if feature disabled)
        """
        if not config.enabled:
            return {}

        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        all_diags: list[dict[str, Any]] = []
        manifests: list[dict[str, Any]] = []

        for inp in inputs:
            try:
                result = self._builder.build(inp, adapter)
                all_nodes.extend(result.get("x86_nodes") or [])
                all_edges.extend(result.get("x86_edges") or [])
                all_diags.extend(result.get("x86_diagnostics") or [])
                if result.get("x86_manifest"):
                    manifests.append(result["x86_manifest"])
            except Exception as exc:  # noqa: BLE001
                # Adapter error degrades only x86 part
                all_diags.append(x86_diag_dict(
                    ADAPTER_ERROR,
                    f"pipeline error for input {inp.source_path!r}: {exc}",
                    severity="degraded",
                ))

        return {
            "x86_nodes": all_nodes,
            "x86_edges": all_edges,
            "x86_diagnostics": all_diags,
            "x86_manifest_list": manifests,
        }
