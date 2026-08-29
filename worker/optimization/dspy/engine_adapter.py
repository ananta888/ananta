"""The only production module allowed to import DSPy, and only lazily."""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from typing import Any

from ananta_contracts.dspy_optimization import OptimizationSpecV1, PromptProgramV1
from worker.optimization.dspy.artifact_serializer import DspyJsonProgramSerializer
from worker.optimization.dspy.compatibility import DspyCompatibilityAdapter
from worker.optimization.dspy.optimizer_registry import DspyOptimizerRegistry


class DspyOptimizationEngineAdapter:
    """Compiles allowlisted few-shot programs; it receives no task or promotion port."""

    _SIGNATURES = {
        "planning_structured_tasks": "goal, constraints -> tasks",
        "rag_answer": "question, context -> answer, citations",
        "structured_extraction": "input -> result",
    }

    def __init__(self, *, optimizer_config: Mapping[str, Any], dspy_lm: object | None = None) -> None:
        self._optimizer_config = dict(optimizer_config)
        self._dspy_lm = dspy_lm
        self._optimizers = DspyOptimizerRegistry()
        self._serializer = DspyJsonProgramSerializer()

    def optimize(
        self, spec: OptimizationSpecV1, baseline: PromptProgramV1, records: Sequence[Mapping[str, Any]]
    ) -> PromptProgramV1:
        capability = DspyCompatibilityAdapter().inspect()
        if capability["state"] != "available":
            raise RuntimeError(str(capability["reason_code"]))
        dspy = importlib.import_module("dspy")
        config = self._optimizers.validate(spec.optimizer_id, self._optimizer_config)
        signature = self._SIGNATURES[spec.program_kind]
        module = dspy.Predict(signature)
        input_names = signature.split("->", maxsplit=1)[0].replace(" ", "").split(",")
        examples = [dspy.Example(**dict(record)).with_inputs(*input_names) for record in records]
        if spec.optimizer_id == "labeled_few_shot":
            optimizer = dspy.LabeledFewShot(k=config["max_labeled_demos"])
        else:
            optimizer = dspy.BootstrapFewShot(**config)
        if self._dspy_lm is None and spec.optimizer_id != "labeled_few_shot":
            raise PermissionError("dspy_authorized_lm_required")
        context = dspy.context(lm=self._dspy_lm) if self._dspy_lm is not None else _NullContext()
        with context:
            compiled = optimizer.compile(module, trainset=examples)
        state = self._state_only(compiled, spec.program_kind)
        return self._serializer.export(
            tenant_id=spec.tenant_id,
            program_id=baseline.program_id,
            program_kind=spec.program_kind,
            dspy_state=state,
            model_roles=baseline.model_roles,
            dspy_version=DspyCompatibilityAdapter.SUPPORTED_VERSION,
        )

    @staticmethod
    def _state_only(compiled: object, program_kind: str) -> Mapping[str, Any]:
        if not hasattr(compiled, "named_predictors"):
            raise RuntimeError("dspy_state_export_unavailable")
        predictors = list(compiled.named_predictors())
        if not predictors or len(predictors) > 16:
            raise RuntimeError("dspy_state_export_invalid")
        expected = {
            "planning_structured_tasks": (("goal", "constraints"), ("tasks",)),
            "rag_answer": (("question", "context"), ("answer", "citations")),
            "structured_extraction": (("input",), ("result",)),
        }[program_kind]
        graph: list[dict[str, Any]] = []
        signatures: list[dict[str, Any]] = []
        demonstrations: list[dict[str, Any]] = []
        for index, (name, predictor) in enumerate(predictors):
            signature = getattr(predictor, "signature", None)
            input_fields = tuple(getattr(signature, "input_fields", {}) or {})
            output_fields = tuple(getattr(signature, "output_fields", {}) or {})
            if (input_fields, output_fields) != expected:
                raise RuntimeError("dspy_state_signature_incompatible")
            module_name = type(predictor).__name__.lower()
            module_kind = "chain_of_thought" if "chainofthought" in module_name else "predict"
            node_id = str(name or f"predictor-{index}").replace(".", "-")
            graph.append(
                {
                    "id": node_id,
                    "module": module_kind,
                    "inputs": list(input_fields),
                    "outputs": list(output_fields),
                    "depends_on": [],
                }
            )
            instructions = str(getattr(signature, "instructions", "") or "")
            signatures.append(
                {
                    "id": f"signature-{index}",
                    "instructions": instructions,
                    "input_fields": list(input_fields),
                    "output_fields": list(output_fields),
                }
            )
            for demo in list(getattr(predictor, "demos", ()) or ()):
                raw = demo.toDict() if hasattr(demo, "toDict") else dict(demo) if isinstance(demo, Mapping) else None
                if raw is None:
                    raise RuntimeError("dspy_state_demo_incompatible")
                allowed = {*input_fields, *output_fields}
                if set(raw) - allowed:
                    raise RuntimeError("dspy_state_demo_unknown_field")
                demonstrations.append({"signature_id": f"signature-{index}", **dict(raw)})
        return {
            "module_graph": graph,
            "signatures": signatures,
            "demonstrations": demonstrations,
            "metadata": {"state_format": "ananta-dspy-projection-v1"},
        }


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


__all__ = ["DspyOptimizationEngineAdapter"]
