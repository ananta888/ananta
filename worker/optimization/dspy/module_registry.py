"""Closed Phase-1 signature and module graph registry."""

from __future__ import annotations

from typing import Any, Mapping


class DspyModuleRegistry:
    _MODULES = frozenset({"predict", "chain_of_thought", "rag_composite"})
    _FIELDS = {
        "planning_structured_tasks": ({"goal", "constraints"}, {"tasks"}),
        "rag_answer": ({"question", "context"}, {"answer", "citations"}),
        "structured_extraction": ({"input"}, {"result"}),
    }

    def validate(
        self, *, program_kind: str, module_graph: list[Mapping[str, Any]], signatures: list[Mapping[str, Any]]
    ) -> None:
        expected = self._FIELDS.get(program_kind)
        if expected is None or not 1 <= len(module_graph) <= 16 or not 1 <= len(signatures) <= 16:
            raise ValueError("dspy_program_graph_invalid")
        node_ids: set[str] = set()
        for node in module_graph:
            if set(node) - {"id", "module", "inputs", "outputs", "depends_on"}:
                raise ValueError("dspy_program_graph_unknown_field")
            node_id = str(node.get("id") or "")
            module = str(node.get("module") or "")
            dependencies = tuple(str(item) for item in node.get("depends_on") or ())
            if (
                not node_id
                or node_id in node_ids
                or module not in self._MODULES
                or any(item not in node_ids for item in dependencies)
            ):
                raise ValueError("dspy_program_graph_invalid")
            node_ids.add(node_id)
        input_fields, output_fields = expected
        for signature in signatures:
            if set(signature) - {"id", "instructions", "input_fields", "output_fields"}:
                raise ValueError("dspy_signature_unknown_field")
            if (
                set(signature.get("input_fields") or ()) != input_fields
                or set(signature.get("output_fields") or ()) != output_fields
            ):
                raise ValueError("dspy_signature_fields_invalid")
            instructions = str(signature.get("instructions") or "")
            if not instructions or len(instructions) > 16_384:
                raise ValueError("dspy_signature_instructions_invalid")


__all__ = ["DspyModuleRegistry"]
