"""Static tokenizer and prompt-template inspection without code execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from worker.model_intelligence.common import (
    ModelAnalysisError,
    canonical_digest,
    read_bounded_json,
)


@dataclass(frozen=True)
class TokenizerAnalysis:
    schema_version: str
    status: str
    vocabulary_size: int | None
    model_max_length: int | None
    special_tokens: Mapping[str, str]
    normalizer: str | None
    pre_tokenizer: str | None
    prompt_template_status: str
    prompt_template_digest: str | None

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "schema_version": self.schema_version,
            "status": self.status,
            "vocabulary_size": self.vocabulary_size,
            "model_max_length": self.model_max_length,
            "special_tokens": dict(sorted(self.special_tokens.items())),
            "normalizer": self.normalizer,
            "pre_tokenizer": self.pre_tokenizer,
            "prompt_template_status": self.prompt_template_status,
            "prompt_template_digest": self.prompt_template_digest,
        }
        body["content_digest"] = canonical_digest(body)
        return body


@dataclass(frozen=True)
class TokenizerAnalysisPolicy:
    max_json_bytes: int = 32 * 1024 * 1024
    max_special_tokens: int = 256

    def __post_init__(self) -> None:
        if self.max_json_bytes <= 0 or self.max_special_tokens <= 0:
            raise ValueError("tokenizer analysis limits must be positive")


class TokenizerAnalyzer:
    def __init__(
        self,
        policy: TokenizerAnalysisPolicy | None = None,
    ) -> None:
        self._policy = policy or TokenizerAnalysisPolicy()

    def analyze(self, *, snapshot_root: str | Path) -> TokenizerAnalysis:
        root = Path(snapshot_root).resolve(strict=True)
        tokenizer_path = root / "tokenizer.json"
        config_path = root / "tokenizer_config.json"
        special_path = root / "special_tokens_map.json"
        if not any(
            candidate.is_file()
            for candidate in (tokenizer_path, config_path, special_path)
        ):
            return TokenizerAnalysis(
                schema_version="tokenizer_analysis.v1",
                status="not_available",
                vocabulary_size=None,
                model_max_length=None,
                special_tokens={},
                normalizer=None,
                pre_tokenizer=None,
                prompt_template_status="not_available",
                prompt_template_digest=None,
            )

        tokenizer = self._optional_json(root, "tokenizer.json")
        config = self._optional_json(root, "tokenizer_config.json")
        special = self._optional_json(root, "special_tokens_map.json")
        vocabulary_size = self._vocabulary_size(tokenizer, config)
        max_length = self._bounded_integer(config.get("model_max_length"))
        special_tokens = self._special_tokens(special, config)
        template = config.get("chat_template")
        if isinstance(template, str) and template:
            template_status = "available"
            template_digest = canonical_digest(template)
        elif template is None:
            template_status = "not_available"
            template_digest = None
        else:
            template_status = "invalid"
            template_digest = None
        return TokenizerAnalysis(
            schema_version="tokenizer_analysis.v1",
            status="available",
            vocabulary_size=vocabulary_size,
            model_max_length=max_length,
            special_tokens=special_tokens,
            normalizer=self._component_type(tokenizer.get("normalizer")),
            pre_tokenizer=self._component_type(
                tokenizer.get("pre_tokenizer")
            ),
            prompt_template_status=template_status,
            prompt_template_digest=template_digest,
        )

    def _optional_json(
        self,
        root: Path,
        relative_path: str,
    ) -> Mapping[str, object]:
        if not (root / relative_path).is_file():
            return {}
        return read_bounded_json(
            root,
            relative_path,
            max_bytes=self._policy.max_json_bytes,
        )

    @staticmethod
    def _vocabulary_size(
        tokenizer: Mapping[str, object],
        config: Mapping[str, object],
    ) -> int | None:
        model = tokenizer.get("model")
        if isinstance(model, Mapping):
            vocabulary = model.get("vocab")
            if isinstance(vocabulary, Mapping):
                return len(vocabulary)
            if isinstance(vocabulary, list):
                return len(vocabulary)
        return TokenizerAnalyzer._bounded_integer(config.get("vocab_size"))

    def _special_tokens(
        self,
        special: Mapping[str, object],
        config: Mapping[str, object],
    ) -> Mapping[str, str]:
        result: dict[str, str] = {}
        candidates = dict(config)
        candidates.update(special)
        for key, value in sorted(candidates.items()):
            if not key.endswith("_token"):
                continue
            normalised = self._token_value(value)
            if normalised is not None:
                result[key] = normalised
            if len(result) > self._policy.max_special_tokens:
                raise ModelAnalysisError(
                    "tokenizer_special_token_count_exceeded",
                    "Tokenizer special-token count exceeds the configured limit.",
                )
        return result

    @staticmethod
    def _token_value(value: object) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            content = value.get("content")
            if isinstance(content, str):
                return content
        return None

    @staticmethod
    def _bounded_integer(value: object) -> int | None:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= 2**31 - 1
        ):
            return value
        return None

    @staticmethod
    def _component_type(value: object) -> str | None:
        if not isinstance(value, Mapping):
            return None
        component_type = value.get("type")
        return component_type if isinstance(component_type, str) else None
