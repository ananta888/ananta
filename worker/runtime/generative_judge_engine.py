"""Local engine boundary for the isolated generative-judge worker."""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from ananta_contracts.generative_judge_worker import GenerativeJudgeWorkerRequest


class GenerativeJudgeEngine(Protocol):
    """Execute one local selection; engines never delegate or orchestrate."""

    @property
    def engine_id(self) -> str: ...

    def select(self, request: GenerativeJudgeWorkerRequest) -> str: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise RuntimeError("local judge engine redirect is forbidden")


class LoopbackGenerativeJudgeEngine:
    """Optional adapter to a model server in this worker container only."""

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str = "",
        max_response_bytes: int = 64 * 1024,
        opener: Any | None = None,
    ) -> None:
        self._endpoint = _literal_loopback_endpoint(endpoint)
        if not 1024 <= int(max_response_bytes) <= 1024 * 1024:
            raise ValueError("local judge engine response limit is invalid")
        self._token = str(bearer_token or "").strip()
        self._max_response_bytes = int(max_response_bytes)
        self._opener = opener or urllib.request.build_opener(_NoRedirectHandler())

    @property
    def engine_id(self) -> str:
        return "local-loopback-model"

    def select(self, request: GenerativeJudgeWorkerRequest) -> str:
        remaining_seconds = (request.deadline_epoch_ms - time.time_ns() // 1_000_000) / 1000.0
        if remaining_seconds <= 0:
            raise TimeoutError("judge engine deadline expired")
        body = json.dumps(
            {
                "schema_version": "1.0",
                "request_id": request.request_id,
                "task_id": request.task_id,
                "baseline_choice_id": request.baseline_choice_id,
                "candidates": [candidate.to_dict() for candidate in request.candidates],
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        engine_request = urllib.request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            response = self._opener.open(engine_request, timeout=min(remaining_seconds, 60.0))
            payload = self._read_response(response)
        except urllib.error.HTTPError as exc:
            payload = self._read_response(exc)
        if set(payload) == {"schema_version", "choice_id"} and payload.get("schema_version") == "1.0":
            selected = payload.get("choice_id")
            if isinstance(selected, str) and selected in {item.choice_id for item in request.candidates}:
                return selected
        if set(payload) == {"schema_version", "corrected_text"} and payload.get("schema_version") == "1.0":
            corrected = payload.get("corrected_text")
            if isinstance(corrected, str):
                matches: list[str] = [
                    str(item.choice_id) for item in request.candidates if item.text == corrected
                ]
                if len(matches) == 1:
                    return matches[0]
        raise ValueError("local judge engine returned an unprovenanced choice")

    def _read_response(self, response: Any) -> Mapping[str, object]:
        if "application/json" not in str(response.headers.get("Content-Type") or "").lower():
            raise ValueError("local judge engine response must be JSON")
        body = response.read(self._max_response_bytes + 1)
        if len(body) > self._max_response_bytes:
            raise ValueError("local judge engine response exceeds its byte limit")
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError("local judge engine response must be an object")
        return value


def _literal_loopback_endpoint(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.startswith("//")
    ):
        raise ValueError("local judge engine endpoint must be explicit loopback HTTP")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("local judge engine endpoint cannot use DNS") from exc
    if not address.is_loopback:
        raise ValueError("local judge engine endpoint must be loopback-local")
    host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return urlunsplit(("http", f"{host}:{parsed.port}", parsed.path or "/", "", ""))


class EmbeddedTransformersGenerativeJudgeEngine:
    """Lazy offline causal-LM engine executed inside this worker process."""

    def __init__(
        self,
        *,
        model_path: str,
        model_root: str,
        device: str = "cpu",
        max_input_chars: int = 64_000,
        max_input_tokens: int = 4_096,
        max_new_tokens: int = 32,
    ) -> None:
        unresolved_root = Path(model_root).expanduser()
        root = unresolved_root.resolve(strict=True)
        unresolved = Path(model_path).expanduser()
        resolved = unresolved.resolve(strict=True)
        if (
            unresolved_root.is_symlink()
            or unresolved.is_symlink()
            or not resolved.is_dir()
            or not resolved.is_relative_to(root)
        ):
            raise ValueError("embedded judge model must be an in-root local directory")
        model_files = tuple(resolved.rglob("*"))
        if not any(path.is_file() for path in model_files) or any(path.is_symlink() for path in model_files):
            raise ValueError("embedded judge model must contain local non-symlink files")
        regular_files = tuple(path for path in model_files if path.is_file())
        forbidden_suffixes = {".bin", ".ckpt", ".pickle", ".pkl", ".pt", ".pth", ".py"}
        if not any(path.suffix.lower() == ".safetensors" for path in regular_files) or any(
            path.suffix.lower() in forbidden_suffixes for path in regular_files
        ):
            raise ValueError("embedded judge model must use safetensors without executable or pickle files")
        if device not in {"cpu", "cuda"}:
            raise ValueError("embedded judge device must be cpu or cuda")
        if not 1024 <= int(max_input_chars) <= 512_000:
            raise ValueError("embedded judge input limit is invalid")
        if not 128 <= int(max_input_tokens) <= 32_768:
            raise ValueError("embedded judge token limit is invalid")
        if not 1 <= int(max_new_tokens) <= 128:
            raise ValueError("embedded judge output limit is invalid")
        self._model_path = resolved
        self._device = device
        self._max_input_chars = int(max_input_chars)
        self._max_input_tokens = int(max_input_tokens)
        self._max_new_tokens = int(max_new_tokens)
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._load_lock = threading.Lock()

    @property
    def engine_id(self) -> str:
        return "embedded-transformers"

    def select(self, request: GenerativeJudgeWorkerRequest) -> str:
        self._remaining_seconds(request)
        prompt = self._prompt(request)
        tokenizer, model = self._runtime()
        self._remaining_seconds(request)
        encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=True)
        self._remaining_seconds(request)
        if self._device == "cuda":
            encoded = {key: value.to("cuda") for key, value in encoded.items()}
        input_length = int(encoded["input_ids"].shape[-1])
        if input_length > self._max_input_tokens:
            raise ValueError("embedded judge tokenized prompt exceeds its limit")
        remaining_seconds = self._remaining_seconds(request)
        generated = model.generate(
            **encoded,
            do_sample=False,
            max_time=remaining_seconds,
            max_new_tokens=self._max_new_tokens,
            pad_token_id=tokenizer.eos_token_id,
        )
        self._remaining_seconds(request)
        generated_tokens = generated[0][input_length:]
        output = str(tokenizer.decode(generated_tokens, skip_special_tokens=True)).strip()
        known_ids = {candidate.choice_id for candidate in request.candidates}
        matches: set[str] = {
            match
            for match in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}", output)
            if match in known_ids
        }
        if len(matches) != 1:
            raise ValueError("embedded judge did not return exactly one known candidate")
        return next(iter(matches))

    @staticmethod
    def _remaining_seconds(request: GenerativeJudgeWorkerRequest) -> float:
        remaining = float(
            (request.deadline_epoch_ms - time.time_ns() // 1_000_000) / 1000.0
        )
        if remaining <= 0:
            raise TimeoutError("embedded judge deadline expired")
        return remaining

    def _runtime(self) -> tuple[Any, Any]:
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model
        with self._load_lock:
            if self._tokenizer is None or self._model is None:
                from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore[import]

                tokenizer = AutoTokenizer.from_pretrained(
                    str(self._model_path),
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model = AutoModelForCausalLM.from_pretrained(
                    str(self._model_path),
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model.eval()
                if self._device == "cuda":
                    model.to("cuda")
                self._tokenizer = tokenizer
                self._model = model
        return self._tokenizer, self._model

    def _prompt(self, request: GenerativeJudgeWorkerRequest) -> str:
        candidates = "\n".join(
            f"{candidate.choice_id}: {candidate.text}" for candidate in request.candidates
        )
        prompt = (
            "Select the single best transcript candidate. Return only its candidate ID.\n"
            f"Baseline: {request.baseline_choice_id}\nCandidates:\n{candidates}\nSelection:"
        )
        if len(prompt) > self._max_input_chars:
            raise ValueError("embedded judge prompt exceeds its configured limit")
        return prompt
