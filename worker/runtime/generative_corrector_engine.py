"""Local model boundary for the isolated generative transcript corrector."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from ananta_contracts.voice_corrector_worker import (
    MAX_CORRECTED_TEXT_CHARS,
    PROMPT_VERSION,
    VoiceCorrectorWorkerRequest,
)

MODEL_CATALOG_VERSION = "ananta.generative-corrector-model-catalog.v1"


@dataclass(frozen=True)
class GenerativeCorrectorModel:
    model_id: str
    path: Path
    revision: str
    family: str


@dataclass(frozen=True)
class GenerativeCorrectorEngineResult:
    corrected_text: str
    model_id: str
    model_revision: str
    engine_id: str
    prompt_version: str = PROMPT_VERSION


class GenerativeCorrectorEngine(Protocol):
    """Execute one local rewrite; engines never delegate or orchestrate."""

    @property
    def engine_id(self) -> str: ...

    @property
    def model_ids(self) -> tuple[str, ...]: ...

    def correct(self, request: VoiceCorrectorWorkerRequest) -> GenerativeCorrectorEngineResult: ...


class EmbeddedTransformersGenerativeCorrectorEngine:
    """Lazy offline causal-LM engine with a strict local model allowlist."""

    def __init__(
        self,
        *,
        model_root: str,
        catalog_path: str,
        device: str = "cpu",
        max_input_chars: int = 32_000,
        max_input_tokens: int = 4_096,
        max_new_tokens: int = 1_024,
    ) -> None:
        unresolved_root = Path(model_root).expanduser()
        root = unresolved_root.resolve(strict=True)
        unresolved_catalog = Path(catalog_path).expanduser()
        resolved_catalog = unresolved_catalog.resolve(strict=True)
        if (
            unresolved_root.is_symlink()
            or unresolved_catalog.is_symlink()
            or not root.is_dir()
            or not resolved_catalog.is_file()
            or not resolved_catalog.is_relative_to(root)
        ):
            raise ValueError("corrector catalog must be a non-symlink file under the model root")
        if device not in {"cpu", "cuda"}:
            raise ValueError("embedded corrector device must be cpu or cuda")
        if not 1_024 <= int(max_input_chars) <= 512_000:
            raise ValueError("embedded corrector input limit is invalid")
        if not 128 <= int(max_input_tokens) <= 32_768:
            raise ValueError("embedded corrector token limit is invalid")
        if not 16 <= int(max_new_tokens) <= 4_096:
            raise ValueError("embedded corrector output limit is invalid")
        self._root = root
        self._models = _load_catalog(resolved_catalog, root=root)
        self._device = device
        self._max_input_chars = int(max_input_chars)
        self._max_input_tokens = int(max_input_tokens)
        self._max_new_tokens = int(max_new_tokens)
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._loaded_model_id: str | None = None
        self._load_lock = threading.Lock()

    @property
    def engine_id(self) -> str:
        return "embedded-transformers"

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    def correct(self, request: VoiceCorrectorWorkerRequest) -> GenerativeCorrectorEngineResult:
        remaining = self._remaining_seconds(request)
        model_entry = self._models.get(request.model_id)
        if model_entry is None:
            raise LookupError("requested corrector model is not allowlisted")
        tokenizer, model = self._runtime(model_entry)
        prompt, add_special_tokens = self._prompt(request, tokenizer=tokenizer)
        self._remaining_seconds(request)
        encoded = tokenizer(
            prompt,
            return_tensors="pt",
            add_special_tokens=add_special_tokens,
        )
        self._remaining_seconds(request)
        if self._device == "cuda":
            encoded = {key: value.to("cuda") for key, value in encoded.items()}
        input_length = int(encoded["input_ids"].shape[-1])
        if input_length > self._max_input_tokens:
            raise ValueError("embedded corrector tokenized prompt exceeds its limit")
        remaining = min(remaining, self._remaining_seconds(request))
        generated = model.generate(
            **encoded,
            do_sample=False,
            max_time=remaining,
            max_new_tokens=self._max_new_tokens,
            pad_token_id=tokenizer.eos_token_id,
        )
        self._remaining_seconds(request)
        generated_tokens = generated[0][input_length:]
        raw_output = str(tokenizer.decode(generated_tokens, skip_special_tokens=True)).strip()
        corrected = self._parse_output(raw_output)
        return GenerativeCorrectorEngineResult(
            corrected_text=corrected,
            model_id=model_entry.model_id,
            model_revision=model_entry.revision,
            engine_id=self.engine_id,
        )

    @staticmethod
    def _remaining_seconds(request: VoiceCorrectorWorkerRequest) -> float:
        remaining = (request.deadline_epoch_ms - time.time_ns() // 1_000_000) / 1000.0
        if remaining <= 0:
            raise TimeoutError("embedded corrector deadline expired")
        return float(remaining)

    def _runtime(self, entry: GenerativeCorrectorModel) -> tuple[Any, Any]:
        if self._loaded_model_id == entry.model_id and self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model
        with self._load_lock:
            if self._loaded_model_id != entry.model_id or self._tokenizer is None or self._model is None:
                from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import]

                tokenizer = AutoTokenizer.from_pretrained(
                    str(entry.path),
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model = AutoModelForCausalLM.from_pretrained(
                    str(entry.path),
                    local_files_only=True,
                    trust_remote_code=False,
                    use_safetensors=True,
                    torch_dtype="auto",
                )
                model.eval()
                if self._device == "cuda":
                    model.to("cuda")
                self._tokenizer = tokenizer
                self._model = model
                self._loaded_model_id = entry.model_id
        return self._tokenizer, self._model

    def _prompt(
        self,
        request: VoiceCorrectorWorkerRequest,
        *,
        tokenizer: Any,
    ) -> tuple[str, bool]:
        language = request.language or "auto"
        system_message = (
            "You correct automatic speech-recognition transcripts. "
            "Fix spelling, punctuation, capitalization, and obvious recognition errors only. "
            "Do not summarize, translate, add facts, remove facts, or change names, numbers, IDs, URLs, or code. "
            "Return exactly one JSON object with keys schema_version and corrected_text and no markdown. "
            "schema_version must be 1.0."
        )
        user_message = f"Language: {language}.\nOriginal transcript:\n{request.original_text}"
        apply_chat_template = getattr(tokenizer, "apply_chat_template", None)
        chat_template = getattr(tokenizer, "chat_template", None)
        if callable(apply_chat_template) and chat_template:
            prompt = apply_chat_template(
                [
                    {
                        "role": "user",
                        "content": f"{system_message}\n{user_message}",
                    },
                ],
                tokenize=False,
                add_generation_prompt=True,
            )
            if not isinstance(prompt, str):
                raise ValueError("embedded corrector chat template returned an invalid prompt")
            add_special_tokens = False
        else:
            prompt = f"{system_message}\n{user_message}\nJSON:"
            add_special_tokens = True
        if len(prompt) > self._max_input_chars:
            raise ValueError("embedded corrector prompt exceeds its configured limit")
        return prompt, add_special_tokens

    @staticmethod
    def _parse_output(raw_output: str) -> str:
        try:
            payload = json.loads(raw_output)
        except (TypeError, ValueError) as exc:
            raise ValueError("embedded corrector returned invalid JSON") from exc
        if not isinstance(payload, Mapping) or set(payload) != {"schema_version", "corrected_text"}:
            raise ValueError("embedded corrector response schema is invalid")
        if payload.get("schema_version") != "1.0" or not isinstance(payload.get("corrected_text"), str):
            raise ValueError("embedded corrector response values are invalid")
        corrected = str(payload["corrected_text"])
        if not corrected or len(corrected) > MAX_CORRECTED_TEXT_CHARS or "\x00" in corrected:
            raise ValueError("embedded corrector text is invalid")
        return corrected


def _load_catalog(path: Path, *, root: Path) -> dict[str, GenerativeCorrectorModel]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("corrector model catalog is invalid") from exc
    if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "models"}:
        raise ValueError("corrector model catalog envelope is invalid")
    if raw.get("schema_version") != MODEL_CATALOG_VERSION or not isinstance(raw.get("models"), list):
        raise ValueError("corrector model catalog version is unsupported")
    if not 1 <= len(raw["models"]) <= 64:
        raise ValueError("corrector model catalog size is outside its bounds")
    models: dict[str, GenerativeCorrectorModel] = {}
    for item in raw["models"]:
        if not isinstance(item, Mapping) or set(item) != {"id", "path", "revision", "family"}:
            raise ValueError("corrector model catalog entry is invalid")
        model_id = _catalog_identifier(item.get("id"), field="id")
        revision = _catalog_identifier(item.get("revision"), field="revision")
        family = _catalog_identifier(item.get("family"), field="family")
        raw_relative = str(item.get("path") or "")
        relative = Path(raw_relative)
        if not raw_relative or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("corrector model path must be relative")
        unresolved = root / relative
        resolved = unresolved.resolve(strict=True)
        if unresolved.is_symlink() or not resolved.is_dir() or not resolved.is_relative_to(root):
            raise ValueError("corrector model path must be an in-root directory")
        files = tuple(resolved.rglob("*"))
        regular_files = tuple(candidate for candidate in files if candidate.is_file())
        forbidden_suffixes = {".bin", ".ckpt", ".pickle", ".pkl", ".pt", ".pth", ".py"}
        if (
            not any(candidate.suffix.lower() == ".safetensors" for candidate in regular_files)
            or any(candidate.is_symlink() for candidate in files)
            or any(candidate.suffix.lower() in forbidden_suffixes for candidate in regular_files)
        ):
            raise ValueError("corrector models must use safetensors without symlinks or executable files")
        if model_id in models:
            raise ValueError("corrector model IDs must be unique")
        models[model_id] = GenerativeCorrectorModel(
            model_id=model_id,
            path=resolved,
            revision=revision,
            family=family,
        )
    return models


def _catalog_identifier(value: object, *, field: str) -> str:
    import re

    normalized = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}", normalized):
        raise ValueError(f"corrector model {field} is invalid")
    return normalized
