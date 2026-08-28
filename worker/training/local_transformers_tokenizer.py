"""Offline tokenizer loading with a narrow Transformers v5 compatibility seam."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

_MAX_CONFIGURATION_BYTES = 1024 * 1024


class LocalTokenizerCompatibilityError(RuntimeError):
    """The immutable local snapshot cannot be represented safely."""


def load_local_tokenizer(model_path: Path | str) -> Any:
    """Load one local tokenizer without trust-remote-code or network fallback."""

    from transformers import AutoTokenizer

    root = Path(model_path).resolve(strict=True)
    if not root.is_dir():
        raise LocalTokenizerCompatibilityError("local_tokenizer_root_not_directory")
    configuration_path = root / "tokenizer_config.json"
    configuration = _configuration(configuration_path) if configuration_path.is_file() else {}
    if configuration.get("tokenizer_class") != "TokenizersBackend":
        return AutoTokenizer.from_pretrained(
            str(root),
            local_files_only=True,
            trust_remote_code=False,
        )

    from transformers import PreTrainedTokenizerFast

    tokenizer_file = root / "tokenizer.json"
    template_file = root / "chat_template.jinja"
    if not tokenizer_file.is_file() or not template_file.is_file():
        raise LocalTokenizerCompatibilityError("local_tokenizer_v5_snapshot_incomplete")
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_file=str(tokenizer_file),
        bos_token=_special_token(configuration, "bos_token"),
        eos_token=_special_token(configuration, "eos_token"),
        pad_token=_special_token(configuration, "pad_token"),
        clean_up_tokenization_spaces=bool(configuration.get("clean_up_tokenization_spaces", False)),
        model_max_length=int(configuration.get("model_max_length", 1_000_000_000_000_000_000)),
    )
    tokenizer.chat_template = _bounded_text(template_file)
    return tokenizer


def _configuration(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(_bounded_text(path))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalTokenizerCompatibilityError("local_tokenizer_configuration_invalid") from exc
    if not isinstance(payload, dict):
        raise LocalTokenizerCompatibilityError("local_tokenizer_configuration_invalid")
    return payload


def _bounded_text(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > _MAX_CONFIGURATION_BYTES:
        raise LocalTokenizerCompatibilityError("local_tokenizer_metadata_invalid")
    return path.read_text(encoding="utf-8")


def _special_token(configuration: Mapping[str, Any], name: str) -> str | None:
    value = configuration.get(name)
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("content"), str):
        return str(value["content"])
    raise LocalTokenizerCompatibilityError(f"local_tokenizer_{name}_invalid")


__all__ = ["LocalTokenizerCompatibilityError", "load_local_tokenizer"]
