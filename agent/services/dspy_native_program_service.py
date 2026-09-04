"""DSPy-free rendering and execution of promoted prompt-program artifacts."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from ananta_contracts.dspy_optimization import PromptProgramV1, canonical_digest
from ananta_contracts.dspy_program_registry import DspyModuleRegistry


class NativePromptExecutorPort(Protocol):
    def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        output_schema: str,
    ) -> str: ...


class DspyNativeProgramRenderer:
    _FIELDS = {
        "planning_structured_tasks": (("goal", "constraints"), ("tasks",)),
        "rag_answer": (("question", "context"), ("answer", "citations")),
        "structured_extraction": (("input",), ("result",)),
    }

    def __init__(self) -> None:
        self._registry = DspyModuleRegistry()

    def render(self, program: PromptProgramV1, inputs: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
        input_fields, output_fields = self._FIELDS[program.program_kind]
        if set(inputs) != set(input_fields):
            raise ValueError("dspy_native_input_fields_invalid")
        graph = [dict(value) for value in program.module_graph]
        signatures = [dict(value) for value in program.signatures]
        self._registry.validate(program_kind=program.program_kind, module_graph=graph, signatures=signatures)
        instructions = "\n".join(str(value["instructions"]) for value in signatures)
        output_schema = str(program.scope.get("output_schema") or program.schema)
        system = (
            f"{instructions}\nReturn one JSON object with exactly these fields: {', '.join(output_fields)}. "
            f"Output schema: {output_schema}. Do not emit tools, routing or provider configuration."
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for demonstration in program.demonstrations:
            example = dict(demonstration)
            if set(input_fields) - set(example) or set(output_fields) - set(example):
                raise ValueError("dspy_native_demonstration_invalid")
            messages.extend(
                (
                    {"role": "user", "content": _json({key: example[key] for key in input_fields})},
                    {"role": "assistant", "content": _json({key: example[key] for key in output_fields})},
                )
            )
        messages.append({"role": "user", "content": _json(dict(inputs))})
        if sum(len(value["content"].encode()) for value in messages) > 512_000:
            raise ValueError("dspy_native_prompt_too_large")
        return tuple(messages)

    def parse(self, program: PromptProgramV1, raw: str) -> dict[str, Any]:
        if not isinstance(raw, str) or not raw or len(raw.encode()) > 2_000_000:
            raise ValueError("dspy_native_output_invalid")
        candidate = raw.strip()
        transformations: list[str] = []
        if candidate.startswith("```json") and candidate.endswith("```"):
            candidate = candidate[7:-3].strip()
            transformations.append("strip_json_fence")
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError("dspy_native_output_unparseable") from exc
        if not isinstance(value, dict):
            raise ValueError("dspy_native_output_invalid")
        expected = set(self._FIELDS[program.program_kind][1])
        if set(value) != expected:
            raise ValueError("dspy_native_output_schema_invalid")
        return {
            "value": value,
            "parse_state": "repaired" if transformations else "strict",
            "transformations": transformations,
            "raw_digest": canonical_digest(raw),
            "value_digest": canonical_digest(value),
        }


class DspyNativeProgramRuntime:
    """Executes a promoted program or delegates to the established baseline."""

    def __init__(self, executor: NativePromptExecutorPort, renderer: DspyNativeProgramRenderer | None = None) -> None:
        self._executor = executor
        self._renderer = renderer or DspyNativeProgramRenderer()

    def execute(
        self,
        *,
        program: PromptProgramV1 | None,
        inputs: Mapping[str, Any],
        baseline: Callable[[], Mapping[str, Any]],
    ) -> dict[str, Any]:
        if program is None:
            return self._fallback(baseline, "dspy_program_not_promoted")
        try:
            messages = self._renderer.render(program, inputs)
            output_schema = str(program.scope.get("output_schema") or program.schema)
            raw = self._executor.complete(messages=messages, output_schema=output_schema)
            parsed = self._renderer.parse(program, raw)
        except (KeyError, RuntimeError, TypeError, ValueError):
            return self._fallback(baseline, "dspy_native_program_failed")
        return {
            "value": parsed["value"],
            "variant": "dspy_promoted",
            "program_digest": program.digest,
            "parse_state": parsed["parse_state"],
            "transformations": parsed["transformations"],
            "fallback_used": False,
        }

    @staticmethod
    def _fallback(baseline: Callable[[], Mapping[str, Any]], reason: str) -> dict[str, Any]:
        value = dict(baseline())
        return {
            "value": value,
            "variant": "baseline",
            "program_digest": None,
            "parse_state": "baseline",
            "transformations": [],
            "fallback_used": True,
            "reason_code": reason,
        }


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


__all__ = [
    "DspyNativeProgramRenderer",
    "DspyNativeProgramRuntime",
    "NativePromptExecutorPort",
]
