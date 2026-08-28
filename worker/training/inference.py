"""Isolated, bounded Base+LoRA inference runtime.

This worker module never owns routing or approval.  It verifies a Hub-issued,
approval-bound envelope and loads weights lazily only inside the worker.
"""

from __future__ import annotations

import gc
import hashlib
import importlib.util
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from worker.training.local_transformers_tokenizer import load_local_tokenizer

CONTRACT_VERSION = "ananta.lora-inference.v1"
GENERATION_CAPABILITY = "ml_intern_lora_text_generation"
MANAGEMENT_CAPABILITY = "ml_intern_lora_cache_management"

_ALLOWED_ADAPTER_FILES = frozenset(
    {
        "adapter_config.json",
        "adapter_model.safetensors",
        "adapter_manifest.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "added_tokens.json",
        "generation_config.json",
        "vocab.json",
        "vocab.txt",
        "merges.txt",
        "tokenizer.model",
        "chat_template.json",
    }
)


class LoraInferenceWorkerError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        http_status: int = 422,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.http_status = http_status
        self.retryable = retryable


class LoraModelExecutor(Protocol):
    def availability(self) -> tuple[bool, str]: ...

    def load(self, *, base_model_path: Path, adapter_path: Path) -> Any: ...

    def generate(
        self,
        handle: Any,
        *,
        prompt: str,
        max_new_tokens: int,
        temperature: float,
    ) -> str: ...

    def unload(self, handle: Any) -> None: ...


class PeftLoraModelExecutor:
    """Lazy PEFT implementation; imports ML packages only during execution."""

    def availability(self) -> tuple[bool, str]:
        missing = [
            package for package in ("torch", "transformers", "peft") if importlib.util.find_spec(package) is None
        ]
        if missing:
            return False, "missing packages: " + ", ".join(missing)
        return True, "local PEFT runtime available"

    def load(self, *, base_model_path: Path, adapter_path: Path) -> tuple[Any, Any]:
        try:
            from peft import PeftModel
            from transformers import AutoModelForCausalLM
        except ImportError as exc:  # pragma: no cover - guarded by availability
            raise LoraInferenceWorkerError(
                "inference_dependency_unavailable",
                "PEFT inference dependencies are unavailable",
                http_status=503,
                retryable=True,
            ) from exc

        model = AutoModelForCausalLM.from_pretrained(
            str(base_model_path),
            device_map="auto",
            local_files_only=True,
            trust_remote_code=False,
        )
        tokenizer = load_local_tokenizer(base_model_path)
        if getattr(tokenizer, "pad_token", None) is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = PeftModel.from_pretrained(
            model,
            str(adapter_path),
            is_trainable=False,
            local_files_only=True,
        )
        model.eval()
        return model, tokenizer

    def generate(
        self,
        handle: tuple[Any, Any],
        *,
        prompt: str,
        max_new_tokens: int,
        temperature: float,
    ) -> str:
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - guarded by availability
            raise LoraInferenceWorkerError(
                "inference_dependency_unavailable",
                "Torch is unavailable",
                http_status=503,
                retryable=True,
            ) from exc
        model, tokenizer = handle
        inputs = tokenizer(prompt, return_tensors="pt")
        if hasattr(model, "device"):
            inputs = {key: value.to(model.device) for key, value in inputs.items()}
        generation: dict[str, Any] = {
            "max_new_tokens": max_new_tokens,
            "do_sample": temperature > 0,
            "pad_token_id": getattr(tokenizer, "eos_token_id", None),
        }
        if temperature > 0:
            generation["temperature"] = temperature
        with torch.no_grad():
            output_ids = model.generate(**inputs, **generation)
        input_length = int(inputs["input_ids"].shape[-1])
        generated_ids = output_ids[0][input_length:]
        return str(tokenizer.decode(generated_ids, skip_special_tokens=True)).strip()

    def unload(self, handle: Any) -> None:
        del handle
        gc.collect()
        if importlib.util.find_spec("torch") is not None:
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass


@dataclass(frozen=True)
class LoraInferenceRuntimeConfiguration:
    workspace_root: Path
    model_root: Path
    resource_profile: str
    max_loaded_adapters: int = 1
    max_prompt_chars: int = 1_048_576
    max_response_chars: int = 4_194_304
    max_adapter_bytes: int = 2 * 1024**3


@dataclass
class _LoadedAdapter:
    adapter_id: str
    version: str
    adapter_digest: str
    model_digest: str
    handle: Any
    last_used_at: float


class LoraInferenceWorkerRuntime:
    """Execute one Hub-authorized inference request; never route or enqueue."""

    def __init__(
        self,
        config: LoraInferenceRuntimeConfiguration,
        executor: LoraModelExecutor | None = None,
    ) -> None:
        if config.resource_profile not in {"cpu", "nvidia"}:
            raise ValueError("LoRA inference requires a cpu or nvidia resource profile")
        if not 1 <= config.max_loaded_adapters <= 16:
            raise ValueError("max_loaded_adapters is outside its bounds")
        self._config = config
        self._workspace_root = _existing_root(config.workspace_root, "workspace_root")
        self._model_root = _existing_root(config.model_root, "model_root")
        self._executor = executor or PeftLoraModelExecutor()
        self._cache: dict[tuple[str, str], _LoadedAdapter] = {}
        self._lock = threading.RLock()

    def capabilities(self) -> dict[str, Any]:
        available, detail = self._executor.availability()
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "ready" if available else "degraded",
            "available": available,
            "reason_code": "lora_inference_ready" if available else "lora_inference_dependency_unavailable",
            "detail": detail,
            "resource_profile": self._config.resource_profile,
            "capabilities": ([GENERATION_CAPABILITY, MANAGEMENT_CAPABILITY] if available else []),
            "limits": {
                "max_loaded_adapters": self._config.max_loaded_adapters,
                "max_prompt_chars": self._config.max_prompt_chars,
                "max_response_chars": self._config.max_response_chars,
            },
        }

    def generate(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        request = self._parse(envelope)
        self._check_deadline(request["deadline_epoch_ms"])
        available, _detail = self._executor.availability()
        if not available:
            raise LoraInferenceWorkerError(
                "inference_dependency_unavailable",
                "LoRA inference runtime is unavailable",
                http_status=503,
                retryable=True,
            )
        base_path = self._resolve(self._model_root, request["base_model"]["relative_path"], "model_missing")
        if _path_sha256(base_path) != request["base_model"]["snapshot_hash"]:
            raise LoraInferenceWorkerError("base_model_hash_mismatch", "base model snapshot hash mismatch")
        adapter_path = self._resolve(
            self._workspace_root,
            request["adapter"]["relative_path"],
            "adapter_missing",
        )
        self._verify_adapter(adapter_path, request)
        self._check_deadline(request["deadline_epoch_ms"])

        key = (request["base_model"]["snapshot_hash"], request["adapter"]["directory_sha256"])
        with self._lock:
            loaded = self._cache.get(key)
            if loaded is None:
                self._evict_for_capacity()
                try:
                    handle = self._executor.load(base_model_path=base_path, adapter_path=adapter_path)
                except LoraInferenceWorkerError:
                    raise
                except Exception as exc:
                    raise LoraInferenceWorkerError(
                        "adapter_load_failed",
                        "approved adapter could not be loaded",
                        http_status=503,
                        retryable=True,
                    ) from exc
                loaded = _LoadedAdapter(
                    adapter_id=request["adapter"]["adapter_id"],
                    version=request["adapter"]["version"],
                    adapter_digest=request["adapter"]["directory_sha256"],
                    model_digest=request["base_model"]["snapshot_hash"],
                    handle=handle,
                    last_used_at=time.time(),
                )
                self._cache[key] = loaded
            elif (
                loaded.adapter_id != request["adapter"]["adapter_id"] or loaded.version != request["adapter"]["version"]
            ):
                raise LoraInferenceWorkerError("adapter_cache_binding_mismatch", "cached adapter identity mismatch")
            loaded.last_used_at = time.time()
            try:
                text = self._executor.generate(
                    loaded.handle,
                    prompt=request["prompt"],
                    max_new_tokens=request["generation"]["max_new_tokens"],
                    temperature=request["generation"]["temperature"],
                )
            except LoraInferenceWorkerError:
                raise
            except Exception as exc:
                raise LoraInferenceWorkerError(
                    "adapter_generation_failed",
                    "approved adapter inference failed",
                    http_status=503,
                    retryable=True,
                ) from exc
        self._check_deadline(request["deadline_epoch_ms"])
        if len(text) > self._config.max_response_chars:
            raise LoraInferenceWorkerError("inference_response_too_large", "inference response exceeds its limit")
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": request["request_id"],
            "task_id": request["task_id"],
            "status": "succeeded",
            "capability": GENERATION_CAPABILITY,
            "reason_code": "approved_adapter_inference_succeeded",
            "output": text,
            "adapter_id": request["adapter"]["adapter_id"],
            "adapter_version": request["adapter"]["version"],
            "adapter_sha256": request["adapter"]["directory_sha256"],
            "base_model": request["base_model"]["model_id"],
            "base_model_sha256": request["base_model"]["snapshot_hash"],
            "policy_decision": request["approval"],
        }

    def unload(self, *, adapter_id: str, adapter_version: str) -> dict[str, Any]:
        _identifier(adapter_id, "adapter_id")
        _identifier(adapter_version, "adapter_version")
        unloaded = 0
        with self._lock:
            keys = [
                key
                for key, entry in self._cache.items()
                if entry.adapter_id == adapter_id and entry.version == adapter_version
            ]
            for key in keys:
                entry = self._cache.pop(key)
                self._executor.unload(entry.handle)
                unloaded += 1
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "succeeded",
            "capability": MANAGEMENT_CAPABILITY,
            "reason_code": "adapter_cache_unloaded" if unloaded else "adapter_cache_not_loaded",
            "adapter_id": adapter_id,
            "adapter_version": adapter_version,
            "unloaded_entries": unloaded,
        }

    def close(self) -> None:
        with self._lock:
            entries = list(self._cache.values())
            self._cache.clear()
        for entry in entries:
            self._executor.unload(entry.handle)

    def _parse(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "contract_version",
            "request_id",
            "task_id",
            "deadline_epoch_ms",
            "capability",
            "operation",
            "task_kind",
            "base_model",
            "adapter",
            "approval",
            "prompt",
            "generation",
        }
        if set(envelope) != expected:
            raise LoraInferenceWorkerError("invalid_contract_shape", "inference envelope fields are invalid")
        if envelope.get("contract_version") != CONTRACT_VERSION:
            raise LoraInferenceWorkerError("contract_version_mismatch", "inference contract version mismatch")
        if envelope.get("capability") != GENERATION_CAPABILITY or envelope.get("operation") != "generate":
            raise LoraInferenceWorkerError("capability_not_supported", "inference capability is not supported")
        request_id = _identifier(envelope.get("request_id"), "request_id")
        task_id = _identifier(envelope.get("task_id"), "task_id")
        task_kind = _identifier(envelope.get("task_kind"), "task_kind")
        deadline = envelope.get("deadline_epoch_ms")
        if isinstance(deadline, bool) or not isinstance(deadline, int):
            raise LoraInferenceWorkerError("invalid_deadline", "deadline must be an integer")
        now_ms = time.time_ns() // 1_000_000
        if not now_ms < deadline <= now_ms + 300_000:
            raise LoraInferenceWorkerError("invalid_deadline", "deadline is expired or outside its bound")
        prompt = envelope.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > self._config.max_prompt_chars:
            raise LoraInferenceWorkerError("prompt_invalid", "prompt is required and bounded")

        model = _mapping(envelope.get("base_model"), "base_model")
        if set(model) != {"model_id", "relative_path", "snapshot_hash"}:
            raise LoraInferenceWorkerError("invalid_contract_shape", "base_model fields are invalid")
        model_id = str(model.get("model_id") or "").strip()
        if not model_id or len(model_id) > 512:
            raise LoraInferenceWorkerError("base_model_invalid", "base model ID is invalid")
        base_model = {
            "model_id": model_id,
            "relative_path": _relative_ref(model.get("relative_path"), "base_model.relative_path"),
            "snapshot_hash": _sha256(model.get("snapshot_hash"), "base_model.snapshot_hash"),
        }

        adapter_raw = _mapping(envelope.get("adapter"), "adapter")
        if set(adapter_raw) != {"adapter_id", "version", "relative_path", "directory_sha256", "files"}:
            raise LoraInferenceWorkerError("invalid_contract_shape", "adapter fields are invalid")
        raw_files = adapter_raw.get("files")
        if not isinstance(raw_files, list) or not 2 <= len(raw_files) <= 256:
            raise LoraInferenceWorkerError("adapter_manifest_invalid", "adapter file manifest is invalid")
        files: list[dict[str, Any]] = []
        names: set[str] = set()
        total = 0
        for raw in raw_files:
            item = _mapping(raw, "adapter.files[]")
            if set(item) != {"name", "sha256", "size_bytes"}:
                raise LoraInferenceWorkerError("adapter_manifest_invalid", "adapter file entry is invalid")
            name = str(item.get("name") or "")
            if Path(name).name != name or name not in _ALLOWED_ADAPTER_FILES or name in names:
                raise LoraInferenceWorkerError("adapter_manifest_invalid", "adapter filename is invalid")
            size = item.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= self._config.max_adapter_bytes:
                raise LoraInferenceWorkerError("adapter_manifest_invalid", "adapter file size is invalid")
            total += size
            if total > self._config.max_adapter_bytes:
                raise LoraInferenceWorkerError("adapter_too_large", "adapter exceeds its size limit", http_status=413)
            names.add(name)
            files.append({"name": name, "sha256": _sha256(item.get("sha256"), "adapter file hash"), "size_bytes": size})
        if not {"adapter_config.json", "adapter_model.safetensors"}.issubset(names):
            raise LoraInferenceWorkerError("adapter_required_file_missing", "adapter files are incomplete")
        adapter = {
            "adapter_id": _identifier(adapter_raw.get("adapter_id"), "adapter_id"),
            "version": _identifier(adapter_raw.get("version"), "adapter_version"),
            "relative_path": _relative_ref(adapter_raw.get("relative_path"), "adapter.relative_path"),
            "directory_sha256": _sha256(adapter_raw.get("directory_sha256"), "adapter.directory_sha256"),
            "files": files,
        }

        generation_raw = _mapping(envelope.get("generation"), "generation")
        if set(generation_raw) != {"max_new_tokens", "temperature"}:
            raise LoraInferenceWorkerError("invalid_contract_shape", "generation fields are invalid")
        max_tokens = generation_raw.get("max_new_tokens")
        temperature = generation_raw.get("temperature")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 4096:
            raise LoraInferenceWorkerError("generation_limits_invalid", "max_new_tokens is outside its bounds")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise LoraInferenceWorkerError("generation_limits_invalid", "temperature is outside its bounds")
        approval = self._approval(
            envelope.get("approval"),
            adapter=adapter,
            base_model=base_model,
            task_kind=task_kind,
        )
        return {
            "request_id": request_id,
            "task_id": task_id,
            "deadline_epoch_ms": deadline,
            "task_kind": task_kind,
            "base_model": base_model,
            "adapter": adapter,
            "approval": approval,
            "prompt": prompt,
            "generation": {"max_new_tokens": max_tokens, "temperature": float(temperature)},
        }

    @staticmethod
    def _approval(
        raw: Any,
        *,
        adapter: Mapping[str, Any],
        base_model: Mapping[str, Any],
        task_kind: str,
    ) -> dict[str, Any]:
        approval = _mapping(raw, "approval")
        if set(approval) != {
            "policy_version",
            "decision",
            "reason_code",
            "approved_only",
            "binding",
            "binding_sha256",
        }:
            raise LoraInferenceWorkerError("approval_binding_invalid", "approval decision fields are invalid")
        if (
            approval.get("policy_version") != "mlintern-lora-runtime-v2"
            or approval.get("decision") != "adapter_selected"
            or approval.get("approved_only") is not True
        ):
            raise LoraInferenceWorkerError("adapter_not_approved", "Hub policy did not approve this adapter")
        binding = _mapping(approval.get("binding"), "approval.binding")
        expected = {
            "adapter_id": adapter["adapter_id"],
            "adapter_version": adapter["version"],
            "adapter_sha256": adapter["directory_sha256"],
            "base_model": base_model["model_id"],
            "task_kind": task_kind,
            "status": "approved",
            "approved_at": str(binding.get("approved_at") or "").strip(),
        }
        if dict(binding) != expected or not expected["approved_at"]:
            raise LoraInferenceWorkerError("approval_binding_mismatch", "approval identity binding is invalid")
        if _canonical_sha256(expected) != _sha256(approval.get("binding_sha256"), "approval binding hash"):
            raise LoraInferenceWorkerError("approval_binding_mismatch", "approval binding hash mismatch")
        return dict(approval)

    def _verify_adapter(self, adapter_path: Path, request: Mapping[str, Any]) -> None:
        if not adapter_path.is_dir() or adapter_path.is_symlink():
            raise LoraInferenceWorkerError("adapter_missing", "adapter path is unavailable")
        actual_paths = []
        for child in adapter_path.iterdir():
            if child.is_symlink() or not child.is_file():
                raise LoraInferenceWorkerError("adapter_tree_invalid", "adapter tree must be flat regular files")
            actual_paths.append(child)
        expected = {item["name"]: item for item in request["adapter"]["files"]}
        if {path.name for path in actual_paths} != set(expected):
            raise LoraInferenceWorkerError("adapter_manifest_mismatch", "adapter files differ from manifest")
        for path in actual_paths:
            row = expected[path.name]
            if path.stat().st_size != row["size_bytes"] or _file_sha256(path) != row["sha256"]:
                raise LoraInferenceWorkerError("adapter_hash_mismatch", "adapter file hash or size mismatch")
        if _directory_manifest_sha256(request["adapter"]["files"]) != request["adapter"]["directory_sha256"]:
            raise LoraInferenceWorkerError("adapter_hash_mismatch", "adapter directory hash mismatch")
        try:
            config = json.loads((adapter_path / "adapter_config.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise LoraInferenceWorkerError("adapter_config_invalid", "adapter config is invalid") from exc
        if not isinstance(config, dict):
            raise LoraInferenceWorkerError("adapter_config_invalid", "adapter config must be an object")
        configured_base = str(
            config.get("base_model_name_or_path") or config.get("base_model") or config.get("base_model_id") or ""
        ).strip()
        if configured_base != request["base_model"]["model_id"]:
            raise LoraInferenceWorkerError("adapter_base_model_mismatch", "adapter base model binding mismatch")
        if str(config.get("peft_type") or "LORA").strip().upper() not in {"LORA", "ADALORA"}:
            raise LoraInferenceWorkerError("adapter_type_not_allowed", "adapter PEFT type is not allowed")
        _validate_safetensors_header(adapter_path / "adapter_model.safetensors")

    def _evict_for_capacity(self) -> None:
        while len(self._cache) >= self._config.max_loaded_adapters:
            key, entry = min(self._cache.items(), key=lambda item: item[1].last_used_at)
            self._cache.pop(key)
            self._executor.unload(entry.handle)

    @staticmethod
    def _check_deadline(deadline_epoch_ms: int) -> None:
        if deadline_epoch_ms <= time.time_ns() // 1_000_000:
            raise LoraInferenceWorkerError("timeout", "inference deadline expired", http_status=504, retryable=True)

    @staticmethod
    def _resolve(root: Path, relative: str, missing_code: str) -> Path:
        current = root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise LoraInferenceWorkerError("invalid_path", "runtime path contains a symlink")
        try:
            candidate = (root / relative).resolve(strict=True)
            candidate.relative_to(root)
        except (FileNotFoundError, ValueError) as exc:
            raise LoraInferenceWorkerError(missing_code, "runtime path is unavailable") from exc
        return candidate


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LoraInferenceWorkerError("invalid_contract_shape", f"{field} must be an object")
    return value


def _identifier(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}", normalized):
        raise LoraInferenceWorkerError("invalid_identifier", f"{field} is invalid")
    return normalized


def _relative_ref(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,511}", normalized)
        or normalized.startswith("/")
        or "//" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise LoraInferenceWorkerError("invalid_path", f"{field} is invalid")
    return normalized


def _sha256(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise LoraInferenceWorkerError("invalid_hash", f"{field} is invalid")
    return normalized


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _directory_manifest_sha256(files: Any) -> str:
    normalized = [
        {"name": str(item["name"]), "sha256": str(item["sha256"]), "size_bytes": int(item["size_bytes"])}
        for item in sorted(files, key=lambda value: str(value["name"]))
    ]
    return _canonical_sha256(normalized)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_sha256(path: Path) -> str:
    if path.is_file() and not path.is_symlink():
        return _file_sha256(path)
    if not path.is_dir() or path.is_symlink():
        raise LoraInferenceWorkerError("model_missing", "model snapshot is unavailable")
    digest = hashlib.sha256()
    entries = list(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise LoraInferenceWorkerError("invalid_path", "model snapshot contains a symlink")
    if any(not item.is_file() and not item.is_dir() for item in entries):
        raise LoraInferenceWorkerError("invalid_path", "model snapshot contains an unsupported entry")
    children = sorted(item for item in entries if item.is_file())
    if not children:
        raise LoraInferenceWorkerError("model_missing", "model snapshot is empty")
    for child in children:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(child).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _validate_safetensors_header(path: Path) -> None:
    size = path.stat().st_size
    if size < 10:
        raise LoraInferenceWorkerError("invalid_safetensors", "adapter safetensors file is invalid")
    with path.open("rb") as handle:
        header_length = int.from_bytes(handle.read(8), "little", signed=False)
        if not 1 < header_length <= min(16 * 1024 * 1024, size - 8):
            raise LoraInferenceWorkerError("invalid_safetensors", "adapter safetensors header is invalid")
        try:
            header = json.loads(handle.read(header_length).decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise LoraInferenceWorkerError("invalid_safetensors", "adapter safetensors header is invalid") from exc
    if not isinstance(header, dict) or not any(key != "__metadata__" for key in header):
        raise LoraInferenceWorkerError("invalid_safetensors", "adapter safetensors contains no tensors")


def _existing_root(path: Path, field: str) -> Path:
    try:
        root = Path(path).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"{field} must exist") from exc
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"{field} must be a regular directory")
    return root


__all__ = [
    "CONTRACT_VERSION",
    "GENERATION_CAPABILITY",
    "MANAGEMENT_CAPABILITY",
    "LoraInferenceRuntimeConfiguration",
    "LoraInferenceWorkerError",
    "LoraInferenceWorkerRuntime",
    "LoraModelExecutor",
    "PeftLoraModelExecutor",
]
