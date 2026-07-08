"""Local Base+LoRA inference service for approved ml_intern adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LoraInferenceError(RuntimeError):
    """Raised when local adapter inference cannot be executed."""


@dataclass(frozen=True)
class LoraInferenceRequest:
    prompt: str
    base_model: str
    adapter_path: str
    max_new_tokens: int = 512
    temperature: float = 0.2
    external_network_allowed: bool = False


class MlInternLoraInferenceService:
    """Runs local PEFT adapter inference for an approved adapter."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, bool], tuple[Any, Any]] = {}

    def generate(self, request: LoraInferenceRequest) -> str:
        if not request.prompt.strip():
            raise LoraInferenceError("prompt is required")
        adapter_dir = Path(request.adapter_path).expanduser().resolve()
        if not adapter_dir.exists() or not adapter_dir.is_dir():
            raise LoraInferenceError(f"adapter_path not found: {adapter_dir}")

        model, tokenizer = self._load_model(
            base_model=request.base_model,
            adapter_path=str(adapter_dir),
            external_network_allowed=request.external_network_allowed,
        )
        try:
            import torch
        except ImportError as exc:
            raise LoraInferenceError("torch is required for lora inference") from exc

        inputs = tokenizer(request.prompt, return_tensors="pt")
        if hasattr(model, "device"):
            inputs = {key: value.to(model.device) for key, value in inputs.items()}
        with torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=max(1, min(int(request.max_new_tokens), 4096)),
                do_sample=request.temperature > 0,
                temperature=max(0.0, float(request.temperature)),
                pad_token_id=getattr(tokenizer, "eos_token_id", None),
            )
        decoded = tokenizer.decode(output_ids[0], skip_special_tokens=True)
        if decoded.startswith(request.prompt):
            decoded = decoded[len(request.prompt):]
        return decoded.strip()

    def _load_model(
        self,
        *,
        base_model: str,
        adapter_path: str,
        external_network_allowed: bool,
    ) -> tuple[Any, Any]:
        key = (base_model, adapter_path, bool(external_network_allowed))
        if key in self._cache:
            return self._cache[key]
        try:
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise LoraInferenceError(
                "lora inference requires installed packages: peft, transformers, torch"
            ) from exc

        model = AutoModelForCausalLM.from_pretrained(
            base_model,
            device_map="auto",
            local_files_only=not external_network_allowed,
        )
        tokenizer = AutoTokenizer.from_pretrained(
            base_model,
            local_files_only=not external_network_allowed,
        )
        if getattr(tokenizer, "pad_token", None) is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = PeftModel.from_pretrained(
            model,
            adapter_path,
            local_files_only=not external_network_allowed,
        )
        model.eval()
        self._cache[key] = (model, tokenizer)
        return model, tokenizer


_service_instance: MlInternLoraInferenceService | None = None


def get_lora_inference_service() -> MlInternLoraInferenceService:
    global _service_instance
    if _service_instance is None:
        _service_instance = MlInternLoraInferenceService()
    return _service_instance
