"""Strict action-only facade over approved ML-Intern LoRA inference."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Protocol

from agent.services.ml_intern_lora_inference_contract import LoraInferenceRequest
from agent.services.spreadsheet_output_repair_strategy import SpreadsheetOutputRepairStrategy
from agent.services.spreadsheet_saga_service import SpreadsheetSagaService
from agent.services.spreadsheet_training_task_family import SpreadsheetTrainingTaskFamilyStrategy
from ananta_contracts.spreadsheet_studio import canonical_json, require_id


class SpreadsheetLoraInferencePort(Protocol):
    def generate(self, request: LoraInferenceRequest, *, tenant_id: str, owner_subject: str) -> Any: ...


class SpreadsheetInferenceService:
    def __init__(
        self,
        *,
        documents: SpreadsheetSagaService,
        inference: SpreadsheetLoraInferencePort,
        strategy: SpreadsheetTrainingTaskFamilyStrategy | None = None,
        repair: SpreadsheetOutputRepairStrategy | None = None,
        audit: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._documents = documents
        self._inference = inference
        self._strategy = strategy or SpreadsheetTrainingTaskFamilyStrategy()
        self._repair = repair or SpreadsheetOutputRepairStrategy()
        self._audit = audit or (lambda _event, _details: None)

    def propose_actions(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {
            "schema",
            "document_id",
            "instruction",
            "adapter_id",
            "adapter_version",
            "base_model",
            "task_id",
            "max_new_tokens",
            "temperature",
        }
        if set(payload) != required or payload.get("schema") != "ananta.spreadsheet-inference-command.v1":
            raise ValueError("spreadsheet_inference_fields_invalid")
        document_id = require_id(payload.get("document_id"), "document_id")
        document = self._documents.get_document(
            tenant_id=tenant_id,
            document_id=document_id,
            principal_id=principal_id,
        )
        instruction = str(payload.get("instruction") or "").strip()
        if not 1 <= len(instruction) <= 4_000:
            raise ValueError("spreadsheet_inference_instruction_invalid")
        prompt = canonical_json(
            {
                "schema": "ananta.spreadsheet-inference-prompt.v1",
                "instruction": instruction,
                "output_contract": {
                    "schema": self._strategy.output_schema,
                    "output_schema_digest": self._strategy.schema_digest,
                    "serializer_digest": self._strategy.serializer_digest,
                    "auto_apply": False,
                },
                "document": {
                    "document_id": document_id,
                    "version": document["version"],
                    "snapshot_digest": document["snapshot_digest"],
                    "snapshot": document["snapshot"],
                },
            }
        )
        if len(prompt.encode()) > 1_048_576:
            raise ValueError("spreadsheet_inference_context_too_large")
        result = self._inference.generate(
            LoraInferenceRequest(
                prompt=prompt,
                base_model=str(payload.get("base_model") or ""),
                adapter_id=str(payload.get("adapter_id") or ""),
                adapter_version=str(payload.get("adapter_version") or ""),
                task_kind="spreadsheet_actions",
                task_id=str(payload.get("task_id") or ""),
                max_new_tokens=payload.get("max_new_tokens"),
                temperature=payload.get("temperature"),
            ),
            tenant_id=tenant_id,
            owner_subject=principal_id,
        )
        raw_output = str(result.text)
        repair = self._repair.repair(raw_output)
        try:
            parsed = self._strategy.parse_inference(raw_output)
            repair_applied = False
            repair_reason_code = None
        except ValueError:
            if not repair.applied:
                raise
            parsed = self._strategy.parse_inference(repair.text)
            repair_applied = True
            repair_reason_code = repair.reason_code
            self._audit(
                "spreadsheet_inference_output_repaired",
                {
                    "tenant_id": tenant_id,
                    "principal_id": principal_id,
                    "document_id": document_id,
                    "adapter_id": result.adapter_id,
                    "original_output_digest": repair.original_digest,
                    "repaired_output_digest": repair.repaired_digest,
                    "reason_code": repair.reason_code,
                    "scope_expanded": False,
                    "capability_expanded": False,
                    "policy_expanded": False,
                },
            )
        return {
            "schema": "ananta.spreadsheet-inference-proposal.v1",
            "document_id": document_id,
            "expected_version": document["version"],
            "base_snapshot_digest": document["snapshot_digest"],
            "result": parsed,
            "adapter_id": result.adapter_id,
            "adapter_version": result.adapter_version,
            "worker_id": result.worker_id,
            "reason_code": result.reason_code,
            "repair_applied": repair_applied,
            "repair_reason_code": repair_reason_code,
            "automatic_apply": False,
            "human_intervention_required": False,
        }


__all__ = ["SpreadsheetInferenceService", "SpreadsheetLoraInferencePort"]
