"""Specialist task-family strategy for the existing ML-Intern pipeline (GBMF-004).

Mirrors ``SpreadsheetTrainingTaskFamilyStrategy``: it projects admitted
examples into the JSONL instruction format the LoRA/QLoRA backends already
consume, scores model output against the decision contract, and builds the
``CreateTrainingJobCommand`` payload with the specialist metadata. It never
talks to trainer CLIs or backend UIs — training stays a normal ML-Intern job.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ananta_contracts.specialist_decision import (
    OUTPUT_SCHEMA,
    SpecialistContractError,
    SpecialistDecisionContract,
    canonical_digest,
)
from agent.services.specialist_dataset_split import SplitManifest
from agent.services.specialist_training_examples import TrainingExample

SPECIALIST_TASK_FAMILY = "specialist_decision"
TRAINING_METHODS = frozenset({"lora", "qlora"})


@dataclass(frozen=True, slots=True)
class SpecialistTrainingTaskFamilyStrategy:
    contract: SpecialistDecisionContract
    family: str = SPECIALIST_TASK_FAMILY
    serializer_version: str = "specialist-jsonl-v1"

    # -- records ------------------------------------------------------------

    def instruction(self, example_input: Mapping[str, Any]) -> str:
        return json.dumps(
            {"specialist": self.contract.contract_key, "decision": self.contract.decision, "input": dict(example_input)},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def output(self, label: str, confidence: float | None = None) -> str:
        decision: dict[str, Any] = {"schema": OUTPUT_SCHEMA, "label": self.contract.validate_label(label)}
        if self.contract.confidence.enabled:
            decision["confidence"] = 1.0 if confidence is None else float(confidence)
        return json.dumps(decision, sort_keys=True, separators=(",", ":"))

    def training_record(self, example: TrainingExample) -> dict[str, Any]:
        if example.specialist_id != self.contract.specialist_id or example.contract_version != self.contract.contract_version:
            raise SpecialistContractError("specialist_example_contract_mismatch")
        return {
            "instruction": self.instruction(example.input),
            "output": self.output(example.label),
            "task_kind": self.family,
            "record_digest": example.example_id,
            "lineage_root_id": example.provenance.origin_ref,
            "label_source": example.label_source,
            "contract_version": example.contract_version,
            "group_key": example.group_key,
        }

    def training_records(self, examples: Iterable[TrainingExample]) -> list[dict[str, Any]]:
        return [self.training_record(example) for example in examples]

    def validate_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        if record.get("task_kind") != self.family or record.get("contract_version") != self.contract.contract_version:
            raise ValueError("specialist_training_record_fields_invalid")
        parsed = self.parse_inference(str(record.get("output") or ""))
        try:
            json.loads(str(record.get("instruction") or ""))["input"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError("specialist_training_record_instruction_invalid") from exc
        normalized = dict(record)
        normalized["output"] = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        return normalized

    # -- inference / scoring ------------------------------------------------

    def parse_inference(self, output: str) -> dict[str, Any]:
        try:
            return self.contract.parse_output(output)
        except SpecialistContractError as exc:
            raise ValueError(exc.reason_code) from exc

    def score_output(self, output: str, *, expected_label: str | None = None) -> dict[str, Any]:
        try:
            parsed = self.parse_inference(output)
        except ValueError as exc:
            return {"schema_valid": False, "label": None, "confidence": None, "correct": None, "total": 0.0, "reason_code": str(exc)}
        correct = None if expected_label is None else parsed["label"] == expected_label
        return {
            "schema_valid": True,
            "label": parsed["label"],
            "confidence": parsed["confidence"],
            "correct": correct,
            "total": 1.0 if correct in (None, True) else 0.0,
            "reason_code": None,
        }

    # -- digests / job request ---------------------------------------------

    @property
    def schema_digest(self) -> str:
        return self.contract.digest

    @property
    def serializer_digest(self) -> str:
        return canonical_digest({"family": self.family, "serializer": self.serializer_version, "contract": self.contract.digest})

    def job_request(
        self,
        *,
        dataset_id: str,
        base_model: str,
        method: str,
        backend: str,
        manifest: SplitManifest,
        benchmark_version: str,
        mode: str = "dry_run",
        hyperparameters: Mapping[str, Any] | None = None,
        output_name: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Payload for ``CreateTrainingJobCommand.from_mapping`` (no new control plane)."""
        if method not in TRAINING_METHODS:
            raise ValueError("specialist_training_method_invalid")
        request: dict[str, Any] = {
            "dataset_id": dataset_id,
            "job_type": "train_lora",
            "mode": mode,
            "backend": backend,
            "base_model": base_model,
            "method": method,
            "output_name": output_name or f"{self.contract.specialist_id}-{self.contract.contract_version}",
            "hyperparameters": dict(hyperparameters or {}),
            "task_family": self.family,
            "task_kinds": [self.family],
            "output_schema_digest": self.schema_digest,
            "serializer_digest": self.serializer_digest,
            "specialist": {
                "specialist_id": self.contract.specialist_id,
                "contract_version": self.contract.contract_version,
                "contract_digest": self.contract.digest,
                "dataset_digest": manifest.dataset_digest,
                "holdout_digest": manifest.holdout.holdout_digest,
                "benchmark_version": benchmark_version,
            },
        }
        request.update(dict(extra or {}))
        return request


def score_specialist_output(output: str) -> dict[str, Any]:
    """Contract-agnostic scorer for the ML-Intern eval service registry.

    Without the concrete contract only the closed output shape can be checked;
    label correctness is measured by ``specialist_benchmark`` against the
    frozen holdout.
    """
    try:
        value = json.loads(output)
    except ValueError:
        return {"schema_valid": False, "total": 0.0, "reason_code": "specialist_output_json_invalid"}
    valid = (
        isinstance(value, Mapping)
        and set(map(str, value)) <= {"schema", "label", "confidence"}
        and value.get("schema") in (None, OUTPUT_SCHEMA)
        and isinstance(value.get("label"), str)
        and bool(value.get("label"))
    )
    return {"schema_valid": valid, "total": 1.0 if valid else 0.0, "reason_code": None if valid else "specialist_output_fields_invalid"}
